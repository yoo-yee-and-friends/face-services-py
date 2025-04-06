from app.core.celery_app import celery_app
from app.db.session import SessionLocal
from app.db.models.Photo import Photo
import boto3
import logging
from app.config.settings import settings
from datetime import datetime

logger = logging.getLogger(__name__)


@celery_app.task(name="cleanup_orphaned_files")
def cleanup_orphaned_files():
    """ตรวจสอบและลบไฟล์ที่ไม่มีในฐานข้อมูลออกจาก DigitalOcean Spaces"""
    logger.info("เริ่มต้นการทำความสะอาดไฟล์ที่ไม่มีในฐานข้อมูล")

    # เชื่อมต่อกับ DigitalOcean Spaces
    s3_client = boto3.client('s3',
                             aws_access_key_id=settings.SPACES_ACCESS_KEY_ID,
                             aws_secret_access_key=settings.SPACES_SECRET_ACCESS_KEY,
                             endpoint_url=settings.SPACES_ENDPOINT)

    try:
        # ใช้ pagination เพื่อดึงรายการไฟล์ทีละส่วน
        paginator = s3_client.get_paginator('list_objects_v2')
        pages = paginator.paginate(Bucket='snapgoated')

        deleted_count = 0
        checked_count = 0

        for page in pages:
            if 'Contents' not in page:
                continue

            for obj in page['Contents']:
                key = obj['Key']
                checked_count += 1

                # ข้ามไฟล์ที่อยู่ในโฟลเดอร์ preview หรือ settings
                if '/preview/' in key or '/settings/' in key:
                    continue

                # แยกเส้นทางและชื่อไฟล์
                path_parts = key.split('/')
                if len(path_parts) < 2:
                    continue

                file_name = path_parts[-1]
                file_path = '/'.join(path_parts[:-1]) + '/'

                # ตรวจสอบในฐานข้อมูล
                with SessionLocal() as db:
                    db_file = db.query(Photo).filter(
                        Photo.file_name == file_name,
                        Photo.file_path == file_path
                    ).first()

                    if not db_file:
                        # ลบไฟล์ต้นฉบับและพรีวิว
                        s3_client.delete_object(Bucket='snapgoated', Key=key)
                        preview_key = f"{file_path}preview/{file_name}"
                        s3_client.delete_object(Bucket='snapgoated', Key=preview_key)
                        deleted_count += 1

        return {
            "success": True,
            "checked_files": checked_count,
            "deleted_files": deleted_count,
            "timestamp": datetime.utcnow().isoformat()
        }

    except Exception as e:
        logger.error(f"เกิดข้อผิดพลาดในการทำความสะอาดไฟล์: {str(e)}")
        return {
            "success": False,
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat()
        }