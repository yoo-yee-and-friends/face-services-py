# app/tasks/scheduler.py
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.api.v1.events import cleanup_orphaned_files


def start_scheduler():
    """
    ตั้งค่า scheduler สำหรับงานที่ไม่ได้ใช้ Celery
    Celery beat จะรับผิดชอบงานที่กำหนดใน celery_app.conf.beat_schedule
    """
    print("Starting scheduler for non-Celery tasks...")
    scheduler = BackgroundScheduler()

    scheduler.add_job(
        cleanup_orphaned_files,
        trigger=CronTrigger(hour=0, minute=0),
        id='cleanup_files_job',
        replace_existing=True,
        max_instances=1,
    )

    scheduler.start()
    return scheduler