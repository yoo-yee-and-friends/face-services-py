from celery import Celery
from app.config.settings import settings
import os

# กำหนดค่าพื้นฐาน
celery_app = Celery(
    "snapgoated",
    broker=os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0"),
    backend=os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/0"),
    include=["app.tasks.face_detection"]
)

# ตั้งค่า Celery
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Bangkok",
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    worker_max_memory_per_child=4000000,  # รีสตาร์ท worker เมื่อใช้หน่วยความจำถึง 4GB
)

# กำหนดตารางเวลาทำงานอัตโนมัติ
celery_app.conf.beat_schedule = {
    'cleanup-orphaned-files-midnight': {
        'task': 'app.tasks.maintenance.cleanup_orphaned_files',
        'schedule': 86400.0,  # ทุก 24 ชั่วโมง
        'options': {'expires': 3600}
    },
}