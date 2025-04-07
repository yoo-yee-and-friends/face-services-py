from celery import Celery
from kombu import Exchange, Queue

celery_app = Celery("worker",
                    broker="redis://localhost:6379/0",
                    backend="redis://localhost:6379/0")

# กำหนดคิวแยกชัดเจน
task_queues = (
    Queue('default', Exchange('default'), routing_key='default'),
    Queue('face_detection', Exchange('face_detection'), routing_key='face_detection'),
)

# กำหนด routes สำหรับงานต่างๆ
task_routes = {
    'app.tasks.face_detection.process_image_face_detection': {'queue': 'face_detection'},
    # งานอื่นๆ จะเข้าคิว default โดยอัตโนมัติ
}

celery_app.autodiscover_tasks(['app.tasks'])

celery_app.conf.update(
    task_queues=task_queues,
    task_routes=task_routes,
    worker_prefetch_multiplier=1,  # ลดลงจาก 50 เป็น 1
    task_acks_late=True,  # ยืนยันงานหลังทำเสร็จเท่านั้น
    task_time_limit=3600,  # จำกัดเวลาทำงาน 1 ชั่วโมง
    task_soft_time_limit=3000,  # แจ้งเตือนเมื่อใกล้หมดเวลา
    worker_max_tasks_per_child=50,  # รีสตาร์ทโปรเซสหลังทำงาน 50 ชิ้น
    broker_pool_limit=10,  # จำกัด connection pool
    broker_connection_timeout=30,  # timeout การเชื่อมต่อ redis
    broker_connection_max_retries=5,  # จำนวนลองใหม่สูงสุด
    worker_concurrency=1,  # ทำงานทีละงาน
    task_default_rate_limit='10/m',  # จำกัดอัตราการทำงาน
)