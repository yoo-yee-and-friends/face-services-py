# app/tasks/scheduler.py
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.api.v1.events import cleanup_orphaned_files


def start_scheduler():
    print("Starting scheduler...")
    scheduler = BackgroundScheduler()

    # ตั้งเวลาให้ทำงานทุกๆ 5 นาที
    scheduler.add_job(
        cleanup_orphaned_files,
        trigger=CronTrigger(hour=0, minute=0),
        id='cleanup_files_job',
        replace_existing=True,
        max_instances=1,  # กำหนดให้มีการทำงานแค่ 1 งานในเวลาเดียวกัน
    )

    scheduler.start()
    return scheduler