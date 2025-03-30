import multiprocessing
import os
import psutil
import time
import logging
from contextlib import contextmanager
import signal

# ตั้งค่า logging
logger = logging.getLogger("gunicorn.conf")

# ค่าพื้นฐาน
web_concurrency = int(os.getenv("WEB_CONCURRENCY", multiprocessing.cpu_count()))
max_workers = int(os.getenv("GUNICORN_MAX_WORKERS", multiprocessing.cpu_count() * 2))
min_workers = int(os.getenv("GUNICORN_MIN_WORKERS", 2))

# เพิ่มตัวแปรเกี่ยวกับการจัดการหน่วยความจำ
max_worker_memory_mb = int(os.getenv("MAX_WORKER_MEMORY_MB", 4096))  # จำกัดหน่วยความจำต่อ worker (MB)
preload_app = os.getenv("PRELOAD_APP", "false").lower() == "true"  # โหลดแอพครั้งเดียวเพื่อประหยัดหน่วยความจำ

# การตั้งค่าพื้นฐานสำหรับ Gunicorn
bind = os.getenv("BIND", "0.0.0.0:8000")
worker_class = os.getenv("WORKER_CLASS", "uvicorn.workers.UvicornWorker")
workers = web_concurrency
threads = int(os.getenv("THREADS", 2))
worker_connections = int(os.getenv("GUNICORN_WORKER_CONNECTIONS", 1000))
max_requests = int(os.getenv("MAX_REQUESTS", 1000))
max_requests_jitter = int(os.getenv("MAX_REQUESTS_JITTER", 50))
graceful_timeout = int(os.getenv("GRACEFUL_TIMEOUT", 120))
timeout = int(os.getenv("TIMEOUT", 120))
keepalive = int(os.getenv("KEEP_ALIVE", 5))
worker_tmp_dir = "/dev/shm"

# ตัวแปรควบคุม autoscaling
check_interval = int(os.getenv("AUTOSCALE_CHECK_INTERVAL", 60))  # ตรวจสอบทุก 60 วินาที
cpu_threshold_down = float(os.getenv("CPU_THRESHOLD_DOWN", 30))  # ลด workers เมื่อ CPU ต่ำกว่า 30%
cpu_threshold_up = float(os.getenv("CPU_THRESHOLD_UP", 70))  # เพิ่ม workers เมื่อ CPU สูงกว่า 70%
memory_threshold = float(os.getenv("MEMORY_THRESHOLD", 80))  # แจ้งเตือนเมื่อหน่วยความจำสูงกว่า 80%

# สถานะการ autoscale
last_check_time = 0
last_scaling_time = 0
scaling_cooldown = 180  # รอ 3 นาทีระหว่างการปรับขนาด


def get_system_load():
    """ดึงข้อมูลการใช้งาน CPU และหน่วยความจำ"""
    try:
        cpu = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()

        # คำนวณหน่วยความจำที่เหลือเป็น MB
        available_memory_mb = memory.available / (1024 * 1024)

        return cpu, memory.percent, available_memory_mb
    except Exception as e:
        logger.error(f"Error getting system metrics: {e}")
        return 0, 0, 0


def on_starting(server):
    """เมื่อเริ่มต้น Gunicorn"""
    global last_check_time, last_scaling_time
    last_check_time = time.time()
    last_scaling_time = time.time()
    logger.info(f"Starting with {workers} workers (min={min_workers}, max={max_workers})")

    # ตรวจสอบหน่วยความจำเริ่มต้น
    _, _, available_memory_mb = get_system_load()
    memory_per_worker_estimate = 500  # ประมาณการใช้หน่วยความจำของ worker แต่ละตัว

    if available_memory_mb < memory_per_worker_estimate * workers:
        logger.warning(f"ไม่มีหน่วยความจำเพียงพอสำหรับ {workers} workers. เหลือ: {available_memory_mb:.1f}MB, "
                       f"ต้องการประมาณ: {memory_per_worker_estimate * workers}MB")
        # ปรับจำนวน worker ตามหน่วยความจำที่มี
        adjusted_workers = max(min_workers, int(available_memory_mb / memory_per_worker_estimate))
        logger.warning(f"ปรับจำนวน workers จาก {workers} เป็น {adjusted_workers}")
        server.num_workers = adjusted_workers


def pre_fork(server, worker):
    """ก่อนสร้าง worker processes"""
    pass


def post_fork(server, worker):
    """หลังสร้าง worker processes"""
    # จำกัดหน่วยความจำของ worker
    try:
        import resource
        # จำกัดหน่วยความจำเป็น soft limit (หน่วย bytes)
        resource.setrlimit(resource.RLIMIT_AS,
                         (max_worker_memory_mb * 1024 * 1024,
                          max_worker_memory_mb * 1024 * 1024 * 2))  # soft limit, hard limit
        logger.info(f"Set memory limit for worker {worker.pid} to {max_worker_memory_mb}MB")
    except (ImportError, ValueError) as e:
        logger.warning(f"Could not set memory limit: {e}")


def pre_exec(server):
    """ก่อนที่ Gunicorn จะ re-execute"""
    server.log.info("Forked child, re-executing")


def when_ready(server):
    """เมื่อ Gunicorn พร้อมรับคำขอ"""
    logger.info(f"Server is ready with {len(server.WORKERS)} workers")

    # ตั้งค่า periodic task สำหรับ autoscaling
    @contextmanager
    def monitor_load(server):
        def _monitor_load(signum, frame):
            global last_check_time, last_scaling_time
            current_time = time.time()

            # ตรวจสอบเป็นระยะ
            if current_time - last_check_time >= check_interval:
                try:
                    cpu, memory_percent, available_memory_mb = get_system_load()
                    actual_workers = len(server.WORKERS.keys())
                    logger.info(
                        f"System load - CPU: {cpu:.1f}%, Memory: {memory_percent:.1f}%, Available memory: {available_memory_mb:.1f}MB, Workers: {actual_workers}")

                    # กำหนดหน่วยความจำขั้นต่ำที่ต้องการเหลือไว้ในระบบ
                    min_required_memory_mb = 1000  # ต้องการหน่วยความจำเหลืออย่างน้อย 1GB

                    # ลดจำนวน workers ทันทีถ้าหน่วยความจำเหลือน้อย
                    if available_memory_mb < min_required_memory_mb and actual_workers > min_workers:
                        worker_to_kill = list(server.WORKERS.values())[0]
                        logger.warning(
                            f"Low memory ({available_memory_mb:.1f}MB), reducing workers from {actual_workers} to {actual_workers - 1}")
                        worker_to_kill.kill(signal.SIGTERM)
                        last_scaling_time = current_time
                        last_check_time = current_time
                        signal.alarm(10)
                        return

                    # เช็คว่าควรปรับจำนวน workers หรือไม่
                    if current_time - last_scaling_time >= scaling_cooldown:
                        # ลดจำนวน workers เมื่อ CPU ต่ำ
                        if cpu < cpu_threshold_down and actual_workers > min_workers:
                            worker_to_kill = list(server.WORKERS.values())[0]
                            logger.info(
                                f"Low CPU load ({cpu:.1f}%), reducing workers from {actual_workers} to {actual_workers - 1}")
                            worker_to_kill.kill(signal.SIGTERM)
                            last_scaling_time = current_time
                        # เพิ่มจำนวน workers เมื่อ CPU สูงและมีหน่วยความจำเพียงพอ
                        elif cpu > cpu_threshold_up and actual_workers < max_workers and available_memory_mb > min_required_memory_mb * 2:
                            logger.info(
                                f"High CPU load ({cpu:.1f}%), increasing workers from {actual_workers} to {actual_workers + 1}")
                            server.num_workers += 1
                            server.manage_workers()
                            last_scaling_time = current_time

                    # แจ้งเตือนเมื่อหน่วยความจำสูง
                    if memory_percent > memory_threshold:
                        logger.warning(
                            f"Memory usage is high: {memory_percent:.1f}%, available: {available_memory_mb:.1f}MB")

                except Exception as e:
                    logger.error(f"Error in load monitoring: {e}")

                last_check_time = current_time

            # ตั้งเวลาตรวจสอบครั้งต่อไป
            signal.alarm(10)

        # ตั้งค่า handler และ timer
        old_handler = signal.signal(signal.SIGALRM, _monitor_load)
        signal.alarm(10)  # เริ่มต้นตรวจสอบหลังจาก 10 วินาที

        try:
            yield
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)

    # เริ่มการมอนิเตอร์
    with monitor_load(server):
        # จะดำเนินต่อไปเรื่อยๆ จนกว่า Gunicorn จะปิดตัวลง
        pass


def worker_int(worker):
    """เมื่อ worker ได้รับสัญญาณ INT"""
    worker.log.info(f"Worker received INT: {worker.pid}")


def worker_abort(worker):
    """เมื่อ worker ถูก abort"""
    worker.log.info(f"Worker was aborted: {worker.pid}")


def worker_exit(server, worker):
    """เมื่อ worker ออกจากการทำงาน"""
    worker.log.info(f"Worker exited: {worker.pid}")


def child_exit(server, worker):
    """เมื่อ child process ออกจากการทำงาน"""
    logger.info(f"Child exit: {worker.pid}")