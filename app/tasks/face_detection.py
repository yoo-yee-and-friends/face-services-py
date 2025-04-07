from app.config.settings import settings
from app.core.celery_app import celery_app
from app.db.session import SessionLocal
from app.db.models.Event import Event
from app.db.models.Photo import Photo, PhotoFaceVector
from app.db.models.EventPhoto import EventPhoto
from app.services.digital_oceans import upload_files_to_spaces, generate_presigned_url
import boto3
import io
import asyncio
import numpy as np
import logging
from PIL import Image
from datetime import datetime
logger = logging.getLogger(__name__)


@celery_app.task(bind=True,
                 queue='face_detection',
                 rate_limit='5/m',
                 autoretry_for=(Exception,),
                 retry_backoff=True,
                 retry_kwargs={'max_retries': 3})
def process_image_face_detection(self, file_name, file_path, event_id, user_id):
    try:
        with SessionLocal() as db:
            event = db.query(Event).filter(Event.id == event_id).first()
            if not event:
                logger.error(f"ไม่พบ event ID {event_id}")
                return False

            # สร้าง S3 client
            s3_client = boto3.client('s3',
                                     aws_access_key_id=settings.SPACES_ACCESS_KEY_ID,
                                     aws_secret_access_key=settings.SPACES_SECRET_ACCESS_KEY,
                                     endpoint_url=settings.SPACES_ENDPOINT)

            # ดาวน์โหลดรูปภาพจาก Spaces
            full_path = f"{file_path}/{file_name}"
            image_obj = io.BytesIO()
            s3_client.download_fileobj('snapgoated', full_path, image_obj)
            image_obj.seek(0)

            # ตรวจจับใบหน้า
            from app.services.image_services import detect_faces_with_insightface
            face_vectors = asyncio.run(detect_faces_with_insightface(image_obj, is_main_face=False, max_faces=20))

            # สร้างและอัปโหลดภาพพรีวิว
            image_obj.seek(0)
            with Image.open(image_obj) as img:
                img = img.convert('RGB')
                max_size = (800, 800)
                img.thumbnail(max_size, Image.Resampling.LANCZOS)

                preview_obj = io.BytesIO()
                img.save(preview_obj, format='JPEG', quality=85)
                preview_obj.seek(0)

                preview_key = f"{file_path}/preview/{file_name}"
                upload_files_to_spaces(preview_obj, preview_key)

            # บันทึกข้อมูลในฐานข้อมูล
            photo = db.query(Photo).filter(
                Photo.file_name == file_name,
                Photo.file_path == f"{file_path}/"
            ).first()

            if not photo:
                photo = Photo(
                    file_name=file_name,
                    file_path=f"{file_path}/",
                    uploaded_at=datetime.utcnow(),
                    is_detected_face=True if face_vectors else False,
                    is_face_verified=True
                )
                db.add(photo)
                db.flush()

                event_photo = EventPhoto(
                    event_id=event_id,
                    photo_id=photo.id
                )
                db.add(event_photo)
            else:
                photo.is_detected_face = True if face_vectors else False
                photo.is_face_verified = True

            # บันทึก face vectors
            if face_vectors and len(face_vectors) > 0:
                for vector in face_vectors:
                    if isinstance(vector, np.ndarray) and len(vector) == 512:
                        face_vector = PhotoFaceVector(
                            photo_id=photo.id,
                            vector=vector.tolist()
                        )
                        db.add(face_vector)

            db.commit()
            return True

    except Exception as e:
        logger.error(f"เกิดข้อผิดพลาดในการประมวลผลรูปภาพ {file_name}: {str(e)}")
        self.retry(exc=e, countdown=30, max_retries=3)
        return False

@celery_app.task(name="process_event_images", bind=True)
def process_event_images(self, event_id, user_id):
    """ประมวลผลรูปภาพทั้งหมดในอีเวนต์"""
    try:
        with SessionLocal() as db:
            # อัพเดทสถานะการประมวลผล
            event = db.query(Event).filter(Event.id == event_id).first()
            if not event:
                return {"success": False, "error": "ไม่พบอีเวนต์"}

            event.is_processing_face_detection = True
            db.commit()

            # ดึงข้อมูลรูปภาพที่ยังไม่ได้ประมวลผล
            from app.db.models import EventPhoto
            photos = db.query(Photo).join(EventPhoto, EventPhoto.photo_id == Photo.id) \
                .filter(EventPhoto.event_id == event_id, Photo.is_face_verified == False).all()

            # สร้าง task ย่อยสำหรับแต่ละรูปภาพ
            tasks = []
            for photo in photos:
                task = process_image_face_detection.delay(
                    photo.file_name, photo.file_path.rstrip('/'), event_id, user_id
                )
                tasks.append(task.id)

            return {"success": True, "task_count": len(tasks), "tasks": tasks}

    except Exception as e:
        logger.error(f"เกิดข้อผิดพลาดในการประมวลผลอีเวนต์ {event_id}: {str(e)}")
        # อัพเดทสถานะกลับเมื่อเกิดข้อผิดพลาด
        with SessionLocal() as db:
            event = db.query(Event).filter(Event.id == event_id).first()
            if event:
                event.is_processing_face_detection = False
                db.commit()
        return {"success": False, "error": str(e)}