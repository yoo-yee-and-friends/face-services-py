import asyncio
import base64
import gc
import io
import json
import logging
import time
from contextlib import closing
from datetime import datetime

import boto3
import numpy as np
import psutil
from PIL import Image
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, HTTPException, Query, UploadFile, File, Form, \
    BackgroundTasks
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.db.models.Country import Country
from app.db.models.EventCreditType import EventCreditType
from app.db.models.EventFolder import EventFolder
from app.db.models.EventFolderPhoto import EventFolderPhoto
from app.db.models.EventPhoto import EventPhoto
from app.db.models.EventType import EventType
from app.db.models.PhotoFaceVector import PhotoFaceVector
from app.db.queries.image_queries import insert_face_vector
from app.db.session import get_db, SessionLocal
from app.schemas.event import Credit
from app.schemas.user import Response
from app.security.auth import get_current_active_user
from typing import Dict, Any, Optional

from app.services.digital_oceans import upload_file_to_spaces, generate_presigned_url, create_folder_in_spaces, \
    check_duplicate_name, delete_file_from_spaces, generate_presigned_upload_url

from app.db.models.User import User
from app.db.models.Photo import Photo
from app.db.models.Event import Event
from app.db.models.EventCredit import EventCredit
from app.utils.event_utils import get_event_query, paginate_query, format_event_data
from app.utils.validation import validate_date_format
from app.tasks.face_detection import process_image_face_detection

router = APIRouter()
logger = logging.getLogger(__name__)
active_connections: dict = {}

class UploadProgressLogger:
    def __init__(self, total_files: int, event_id: int):
        self.total_files = total_files
        self.processed_files = 0
        self.successful_files = 0
        self.failed_files = 0
        self.event_id = event_id
        self.start_time = time.time()

    def to_dict(self) -> dict:
        return {
            "total_files": self.total_files,
            "processed_files": self.processed_files,
            "successful_files": self.successful_files,
            "failed_files": self.failed_files,
            "remaining_files": self.total_files - self.processed_files,
            "progress_percentage": round((self.processed_files / self.total_files) * 100, 2),
            "elapsed_time": round(time.time() - self.start_time, 2)
        }


@router.get("/events", response_model=Response)
def get_events(
        page: int = 1,
        limit: int = 10,
        status: Optional[bool] = None,
        search: Optional[str] = None,
        update_processing_status: bool = True,  # พารามิเตอร์ใหม่สำหรับควบคุมการอัพเดทสถานะ
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_active_user)
):
    if page < 1:
        return Response(
            message="Page number must be greater than 0",
            status="error",
            status_code=400
        )

    query = get_event_query(db, current_user, status, search)
    total_events = query.count()
    events = paginate_query(query, page, limit)

    # ตรวจสอบและอัพเดทสถานะการประมวลผลใบหน้า
    if update_processing_status:
        for event in events:
            if event.is_processing_face_detection:
                # ตรวจสอบว่ามีรูปที่ยังไม่ได้ตรวจจับใบหน้าหรือไม่
                unverified_count = db.query(Photo).join(EventPhoto).filter(
                    EventPhoto.event_id == event.id,
                    Photo.is_face_verified == False
                ).count()

                # ถ้าไม่มีรูปที่ยังไม่ได้ตรวจสอบ ให้อัพเดทสถานะของอีเวนต์
                if unverified_count == 0:
                    event.is_processing_face_detection = False
                    db.commit()

    total_pages = (total_events + limit - 1) // limit
    events_data = format_event_data(events)

    return Response(
        message="Events retrieved successfully",
        data={
            "total_events": total_events,
            "total_pages": total_pages,
            "current_page": page,
            "events_per_page": limit,
            "events": events_data
        },
        status="success",
        status_code=200
    )

@router.get("/prepare-event-create", response_model=Response)
def prepare_event_data(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    event_types = jsonable_encoder(db.query(EventType).all())
    countries = jsonable_encoder(db.query(Country).all())
    event_credit_types = jsonable_encoder(db.query(EventCreditType).all())

    return Response(
        message="Data retrieved successfully",
        data={
            "event_types": event_types,
            "countries": countries,
            "event_credit_types": event_credit_types
        },
        status="success",
        status_code=200
    )

@router.post("/create-event", response_model=Response)
def create_event(
    event_name: str = Form(...),
    event_type_id: int = Form(...),
    date: str = Form(...),
    location_name: str = Form(...),
    country_id: int = Form(...),
    city_id: int = Form(...),
    status: Optional[bool] = Form(False),
    cover_photo: UploadFile = File(...),
    credits: str = Form("[]"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    try:
        credit_list = json.loads(credits)
        credit_objects = [Credit(**credit) for credit in credit_list]

        is_validate_date_format = validate_date_format(date)
        if not is_validate_date_format:
            return Response(
                message="Invalid date format. Please use YYYY-MM-DD",
                status="error",
                status_code=400
            )

        event_date = datetime.strptime(date, "%Y-%m-%d")
        publish_at = datetime.utcnow() if status else None
        new_event = Event(
            event_name=event_name,
            event_type_id=event_type_id,
            date=event_date,
            location=location_name,
            country_id=country_id,
            city_id=city_id,
            status=status,
            user_id=current_user.id,
            publish_at=publish_at
        )
        db.add(new_event)
        db.commit()
        db.refresh(new_event)

        try:
            file_path = f"{current_user.id}/{new_event.id}/settings/{cover_photo.filename}"
            cover_photo.file.seek(0)
            file_content = cover_photo.file.read()
            cover_photo_path = upload_file_to_spaces(cover_photo, file_path)
            if not cover_photo_path:
                return Response(
                    message="Error uploading cover photo",
                    status="error",
                    status_code=500
                )

            cover_photo_size = len(file_content)
            new_photo = Photo(
                file_name=cover_photo.filename,
                size=cover_photo_size,
                file_path=f"{current_user.id}/{new_event.id}/settings/",
            )
            db.add(new_photo)
            db.commit()
            db.refresh(new_photo)

            new_event.cover_photo_id = new_photo.id
            db.commit()
        except Exception as e:
            db.delete(new_event)
            db.commit()
            logger.error(f"Error uploading cover photo: {e}")
            return Response(
                message="Error uploading cover photo: " + str(e),
                status="error",
                status_code=500
            )

        for credit in credit_objects:
            new_credit = EventCredit(
                event_id=new_event.id,
                event_credit_type_id=credit.credit_type_id,
                name=credit.name
            )
            db.add(new_credit)
        db.commit()

        return get_events(db=db, current_user=current_user)
    except Exception as e:
        logger.error(f"Error creating event: {e}")
        return Response(
            message="Error creating event: " + str(e),
            status="error",
            status_code=500
        )

@router.get("/event-details", response_model=Response)
def get_event_details(
    event_id: int,
    page: int = 1,
    limit: int = 10,
    search: Optional[str] = None,
    sort_by: Optional[str] = "name",
    sort_order: Optional[str] = "asc",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    if page < 1:
        return Response(
            message="Page number must be greater than 0",
            status="error",
            status_code=400
        )

    event = db.query(Event).filter(Event.id == event_id, Event.user_id == current_user.id).first()
    if not event:
        return Response(
            message="Event not found",
            status="error",
            status_code=404
        )

    # Query event folders
    folder_query = db.query(EventFolder).filter(EventFolder.event_id == event_id)
    if search:
        folder_query = folder_query.filter(EventFolder.name.ilike(f"%{search}%"))

    if sort_by == "name":
        if sort_order == "asc":
            folder_query = folder_query.order_by(EventFolder.name.asc())
        else:
            folder_query = folder_query.order_by(EventFolder.name.desc())
    elif sort_by == "date":
        if sort_order == "asc":
            folder_query = folder_query.order_by(EventFolder.updated_at.asc())
        else:
            folder_query = folder_query.order_by(EventFolder.updated_at.desc())

    total_folders = folder_query.count()
    skip = (page - 1) * limit
    event_folders = folder_query.offset(skip).limit(limit).all()
    total_folder_pages = (total_folders + limit - 1) // limit

    # Query photos related to event
    photo_query = db.query(Photo).join(EventPhoto).filter(EventPhoto.event_id == event_id)
    if search:
        photo_query = photo_query.filter(Photo.file_name.ilike(f"%{search}%"))

    if sort_by == "name":
        if sort_order == "asc":
            photo_query = photo_query.order_by(Photo.file_name.asc())
        else:
            photo_query = photo_query.order_by(Photo.file_name.desc())
    elif sort_by == "date":
        if sort_order == "asc":
            photo_query = photo_query.order_by(Photo.uploaded_at.asc())
        else:
            photo_query = photo_query.order_by(Photo.uploaded_at.desc())

    total_photos = photo_query.count()
    photos = photo_query.offset(skip).limit(limit).all()
    photos_data = [
        {
            "id": photo.id,
            "uploaded_at": photo.uploaded_at,
            "file_name": photo.file_name,
            "preview_url": generate_presigned_url(
                f"{photo.file_path}preview/{photo.file_name}"
            )
        }
        for photo in photos
    ]
    total_photo_pages = (total_photos + limit - 1) // limit

    return Response(
        message="Data retrieved successfully",
        data={
            "event": jsonable_encoder(event),
            "total_folders": total_folders,
            "total_folder_pages": total_folder_pages,
            "folders_per_page": limit,
            "folders": jsonable_encoder(event_folders),
            "total_photos": total_photos,
            "total_photo_pages": total_photo_pages,
            "photos_per_page": limit,
            "photos": jsonable_encoder(photos_data)
        },
        status="success",
        status_code=200
    )

@router.get("/folder-details", response_model=Dict[str, Any])
def get_folder_details(
    folder_id: int,
    page: int = 1,
    limit: int = 10,
    search: Optional[str] = None,
    sort_by: Optional[str] = "name",
    sort_order: Optional[str] = "asc",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    if page < 1:
        return Response(
            message="Page number must be greater than 0",
            status="error",
            status_code=400
        )

    folder = db.query(EventFolder).filter(EventFolder.id == folder_id).first()
    if not folder:
        return Response(
            message="Folder not found",
            status="error",
            status_code=404
        )

    photo_query = db.query(Photo).join(EventFolderPhoto).filter(EventFolderPhoto.event_folder_id == folder_id)
    if search:
        photo_query = photo_query.filter(Photo.file_name.ilike(f"%{search}%"))

    if sort_by == "name":
        if sort_order == "asc":
            photo_query = photo_query.order_by(Photo.file_name.asc())
        else:
            photo_query = photo_query.order_by(Photo.file_name.desc())
    elif sort_by == "date":
        if sort_order == "asc":
            photo_query = photo_query.order_by(Photo.uploaded_at.asc())
        else:
            photo_query = photo_query.order_by(Photo.uploaded_at.desc())

    total_photos = photo_query.count()
    skip = (page - 1) * limit
    photos = photo_query.offset(skip).limit(limit).all()
    photos_data = [
        {
            "id": photo.id,
            "uploaded_at": photo.uploaded_at,
            "file_name": photo.filen_ame.split('/')[-1],
            "preview_url": generate_presigned_url(
                f"{photo.file_path}/preview/{photo.file_name}")
        }
        for photo in photos
    ]
    total_photo_pages = (total_photos + limit - 1) // limit

    return Response(
        message="Data retrieved successfully",
        data={
            "folder": jsonable_encoder(folder),
            "total_photos": total_photos,
            "total_photo_pages": total_photo_pages,
            "photos_per_page": limit,
            "photos": jsonable_encoder(photos_data)
        },
        status="success",
        status_code=200
    )

@router.delete("/delete-event", response_model=Response)
def delete_event(
        event_id: int,
        page: int = 1,
        limit: int = 10,
        status: str = "",  # Changed to str to handle empty string case
        search: Optional[str] = None,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_active_user)
):
    status_filter = None
    if status:
        if status.lower() == "true":
            status_filter = True
        elif status.lower() == "false":
            status_filter = False

    event = db.query(Event).filter(Event.id == event_id, Event.user_id == current_user.id).first()
    if not event:
        return Response(
            message="Event not found",
            status="error",
            status_code=404
        )

    try:
        # Delete cover photo if it exists
        if event.cover_photo_id:
            cover_photo = db.query(Photo).filter(Photo.id == event.cover_photo_id).first()
            if cover_photo:
                # Store file paths before deletion
                cover_paths = [
                    f"{cover_photo.file_path}{cover_photo.file_name}",
                    f"{cover_photo.file_path}preview/{cover_photo.file_name}"
                ]
                for path in cover_paths:
                    delete_file_from_spaces(path)
                db.delete(cover_photo)
                db.commit()

        # Get all photos in event with their paths
        photos = db.query(Photo).join(EventPhoto).filter(EventPhoto.event_id == event_id).all()
        for photo in photos:
            # Store paths before deletion
            photo_paths = [
                f"{photo.file_path}{photo.file_name}",
                f"{photo.file_path}preview/{photo.file_name}"
            ]
            # Delete vectors first
            db.query(PhotoFaceVector).filter(PhotoFaceVector.photo_id == photo.id).delete()
            db.query(EventPhoto).filter(EventPhoto.photo_id == photo.id).delete()
            db.delete(photo)
            db.commit()

            # Delete files after database cleanup
            for path in photo_paths:
                delete_file_from_spaces(path)

        # Handle folders
        folders = db.query(EventFolder).filter(EventFolder.event_id == event_id).all()
        for folder in folders:
            # Get folder photos with their paths
            folder_photos = db.query(Photo).join(EventFolderPhoto).filter(
                EventFolderPhoto.event_folder_id == folder.id
            ).all()

            for photo in folder_photos:
                # Store paths before deletion
                photo_paths = [
                    f"{photo.file_path}{photo.file_name}",
                    f"{photo.file_path}preview/{photo.file_name}"
                ]
                # Delete database records first
                db.query(PhotoFaceVector).filter(PhotoFaceVector.photo_id == photo.id).delete()
                db.query(EventFolderPhoto).filter(
                    EventFolderPhoto.photo_id == photo.id,
                    EventFolderPhoto.event_folder_id == folder.id
                ).delete()
                db.delete(photo)
                db.commit()

                # Delete files after database cleanup
                for path in photo_paths:
                    delete_file_from_spaces(path)

            db.delete(folder)
            db.commit()

        # Finally delete the event
        db.delete(event)
        db.commit()

        # Get filtered events after deletion
        if page < 1:
            return Response(
                message="Page number must be greater than 0",
                status="error",
                status_code=400
            )

        query = get_event_query(db, current_user, status_filter, search)
        total_events = query.count()
        events = paginate_query(query, page, limit)
        total_pages = (total_events + limit - 1) // limit
        events_data = format_event_data(events)

        return Response(
            message="Event deleted successfully",
            status="success",
            status_code=200,
            data={
                "total_events": total_events,
                "total_pages": total_pages,
                "current_page": page,
                "events_per_page": limit,
                "events": events_data,
                "current_search": search or "",
                "current_status": status  # Use the original string value
            }
        )

    except Exception as e:
        db.rollback()
        logger.error(f"Error deleting event: {str(e)}")
        return Response(
            message=f"Error deleting event: {str(e)}",
            status="error",
            status_code=500
        )

@router.post("/batch-upload-urls", response_model=Response)
async def create_upload_urls(
        request: dict,
        current_user: User = Depends(get_current_active_user),
        db: Session = Depends(get_db)
):
    event_id = request.get("eventId")
    images = request.get("images", [])

    if not event_id:
        raise HTTPException(status_code=400, detail="Missing event ID")

    if not images:
        raise HTTPException(status_code=400, detail="No images provided")

    # Verify event exists and belongs to user
    event = db.query(Event).filter(
        Event.id == event_id,
        Event.user_id == current_user.id
    ).first()

    if not event:
        raise HTTPException(
            status_code=404,
            detail="Event not found or doesn't belong to the current user"
        )

    # Generate URLs for each image with duplicate detection
    result = []
    base_path = f"{current_user.id}/{event_id}"

    # Get existing files to check for duplicates
    s3_client = boto3.client('s3',
                             aws_access_key_id=settings.SPACES_ACCESS_KEY_ID,
                             aws_secret_access_key=settings.SPACES_SECRET_ACCESS_KEY,
                             endpoint_url=settings.SPACES_ENDPOINT)

    try:
        existing_files = s3_client.list_objects_v2(Bucket='snapgoated', Prefix=base_path)
        existing_names = [obj['Key'].split('/')[-1] for obj in existing_files.get('Contents', [])]
    except Exception as e:
        logger.error(f"Error checking existing files: {str(e)}")
        existing_names = []

    # Track names we've already processed in this batch
    processed_names = set(existing_names)

    for image in images:
        file_name = image.get("name", "")
        content_type = image.get("content_type", "image/jpeg")

        if not file_name:
            continue

        # แก้ไขชื่อไฟล์เพื่อป้องกันปัญหา
        clean_file_name = sanitize_filename(file_name)

        # ตรวจสอบซ้ำ
        is_duplicate = clean_file_name in processed_names
        new_file_name = clean_file_name

        if is_duplicate:
            # สร้างชื่อใหม่ถ้าซ้ำ
            name, ext = clean_file_name.rsplit('.', 1) if '.' in clean_file_name else (clean_file_name, '')
            counter = 1

            while f"{name}_{counter}.{ext}" in processed_names:
                counter += 1

            new_file_name = f"{name}_{counter}.{ext}" if ext else f"{name}_{counter}"

            # เพิ่มชื่อใหม่ในรายการที่ประมวลผลแล้ว
            processed_names.add(new_file_name)
        else:
            # เพิ่มชื่อเดิมในรายการที่ประมวลผลแล้ว
            processed_names.add(clean_file_name)

        # พาธไฟล์ต้นฉบับ
        file_path = f"{base_path}/{new_file_name}"

        # พาธไฟล์พรีวิว
        preview_file_path = f"{base_path}/preview/{new_file_name}"

        try:
            upload_url = generate_presigned_upload_url(
                file_path,
                3600,
                content_type
            )

            preview_url = generate_presigned_upload_url(
                preview_file_path,
                3600,
                content_type
            )

            result.append({
                "name": file_name,
                "url": upload_url,
                "url_preview": preview_url,
                "isDuplicate": is_duplicate,
                "newFileName": new_file_name
            })

        except Exception as e:
            logger.error(f"Error generating upload URL for {file_name}: {str(e)}")
            return Response(
                message=f"Error generating upload URL for {file_name}: {str(e)}",
                status_code=500,
                status="error"
            )

    return Response(
        message=f"Generated {len(result)} upload URLs successfully",
        status_code=200,
        status="success",
        data={
            "urls": result
        }
    )


@router.post("/process-uploaded-images", response_model=Response)
async def process_uploaded_images(
        request: dict,
        background_tasks: BackgroundTasks,  # เพิ่ม background tasks
        current_user: User = Depends(get_current_active_user),
        db: Session = Depends(get_db)
):
    event_id = request.get("eventId")
    images = request.get("images", [])

    if not event_id or not images:
        return Response(
            message="ข้อมูลไม่ครบถ้วน",
            status_code=400,
            status="error"
        )

    # ตรวจสอบสิทธิ์การเข้าถึง event อย่างรวดเร็ว
    event = db.query(Event).filter(
        Event.id == event_id,
        Event.user_id == current_user.id
    ).first()

    if not event:
        return Response(
            message="ไม่พบอีเวนต์หรือไม่มีสิทธิ์เข้าถึง",
            status_code=404,
            status="error"
        )

    # ตั้งค่าสถานะการประมวลผลเท่านั้น - ไม่ทำการประมวลผลจริงในขณะที่ request ยังเปิดอยู่
    event.is_processing_face_detection = True
    db.commit()

    # แบ่งรูปภาพเป็นชุดๆ ขนาดเล็ก
    BATCH_SIZE = 50
    image_batches = [images[i:i + BATCH_SIZE] for i in range(0, len(images), BATCH_SIZE)]

    # ทำงานในพื้นหลังแทนการประมวลผลทั้งหมดในระหว่าง request
    background_tasks.add_task(
        process_image_batches_background,
        image_batches,
        event_id,
        current_user.id
    )

    return Response(
        message=f"เริ่มประมวลผลรูปภาพ {len(images)} รูปในพื้นหลัง",
        data={
            "total_images": len(images),
            "event_id": event_id,
            "processing_started": True
        },
        status="success",
        status_code=202  # ใช้ 202 Accepted เมื่อการประมวลผลเริ่มต้นแต่ยังไม่เสร็จสมบูรณ์
    )


async def process_image_batches_background(
        image_batches: list,
        event_id: int,
        user_id: int
):
    """ฟังก์ชั่นสำหรับประมวลผลรูปภาพในพื้นหลัง"""
    # สร้าง session ใหม่สำหรับการทำงานในพื้นหลัง
    with SessionLocal() as db:
        try:
            # ดึงข้อมูล event
            event = db.query(Event).filter(Event.id == event_id).first()
            if not event:
                logger.error(f"ไม่พบ event id {event_id}")
                return

            # ประมวลผลรูปภาพทีละชุด
            total_processed = 0
            total_size = 0

            for batch in image_batches:
                # บันทึกข้อมูลรูปภาพเป็นชุด
                photos_to_add = []
                event_photos_to_add = []

                for image in batch:
                    file_name = image.get("name")
                    file_path = f"{user_id}/{event_id}/"

                    new_photo = Photo(
                        file_name=file_name,
                        file_path=file_path,
                        size=image.get("size", 0),
                        is_detected_face=False,
                    )
                    photos_to_add.append(new_photo)

                # บันทึกข้อมูลรูปภาพทั้งชุดในครั้งเดียว
                db.bulk_save_objects(photos_to_add)
                db.flush()

                # สร้างความสัมพันธ์กับ event
                for photo in photos_to_add:
                    event_photos_to_add.append(
                        EventPhoto(event_id=event_id, photo_id=photo.id)
                    )
                    total_size += photo.size

                db.bulk_save_objects(event_photos_to_add)
                db.commit()

                # สั่ง Celery tasks สำหรับประมวลผลใบหน้า
                # ใช้ queue ที่แยกออกมาเพื่อไม่ให้กระทบงานอื่น
                task_batch = []
                for photo in photos_to_add:
                    task = process_image_face_detection.apply_async(
                        args=[
                            photo.file_name,
                            photo.file_path.rstrip('/'),
                            event_id,
                            user_id
                        ],
                        queue='image_processing'  # ใช้ queue แยกสำหรับการประมวลผลรูปภาพ
                    )
                    task_batch.append(task.id)

                total_processed += len(batch)

                # พักเล็กน้อยระหว่างชุดเพื่อไม่ให้ใช้ทรัพยากรมากเกินไป
                await asyncio.sleep(0.5)

            # อัพเดทสถิติของ event
            event.total_image_count += total_processed
            event.total_image_size += total_size
            db.commit()

        except Exception as e:
            logger.error(f"เกิดข้อผิดพลาดในการประมวลผลรูปภาพ: {str(e)}")
            db.rollback()

async def send_heartbeat(websocket: WebSocket):
    """Send periodic heartbeat to keep connection alive"""
    try:
        while True:
            await asyncio.sleep(30)  # Send heartbeat every 30 seconds
            if not websocket.client_disconnected:
                await websocket.send_json({
                    "type": "heartbeat",
                    "timestamp": datetime.utcnow().isoformat()
                })
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"Heartbeat error: {str(e)}")

async def send_upload_progress(
        websocket: WebSocket,
        message: str,
        progress: UploadProgressLogger,
        data: dict = None,
        level: str = "info"
):
    """Send formatted progress update through WebSocket with error handling"""
    try:
        if websocket.client_disconnected:
            return

        log_data = {
            "type": "upload_progress",
            "level": level,
            "message": message,
            "timestamp": datetime.utcnow().isoformat(),
            "progress": progress.to_dict(),
            "data": data
        }
        await websocket.send_json(log_data)
        logger.info(f"Progress sent: {message}")
    except Exception as e:
        logger.error(f"Error sending progress update: {e}")
        # Don't raise the exception to prevent disrupting the upload process

def cleanup_orphaned_files():
    """
    ตรวจสอบและลบไฟล์ที่ไม่���ีในฐานข้อมูลออกจาก DigitalOcean Spaces
    รันทุกวั���ตอนเที่ยงคืน
    """
    logger.info("เริ่มต้นการทำความสะอาดไฟล์ที่ไม่มีในฐานข้อมูล")

    # เชื่อมต่อกับ DigitalOcean Spaces
    s3_client = boto3.client('s3',
                             aws_access_key_id=settings.SPACES_ACCESS_KEY_ID,
                             aws_secret_access_key=settings.SPACES_SECRET_ACCESS_KEY,
                             endpoint_url=settings.SPACES_ENDPOINT)

    # ดึงรายการไฟล์ทั้งหมด (อาจต้องใช้ pagination หากมีไฟล์จำนวนมาก)
    try:
        paginator = s3_client.get_paginator('list_objects_v2')
        pages = paginator.paginate(Bucket='snapgoated')

        deleted_count = 0
        checked_count = 0

        # ตรวจสอบทีละหน้า
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
                        # ไฟล์ไม่มีในฐานข้อมูล ให้ลบทั้งไฟล์ต้นฉบับและพรีวิว
                        try:
                            # ลบไฟล์ต้นฉบับ
                            s3_client.delete_object(Bucket='snapgoated', Key=key)

                            # ลบไฟล์พรีวิว
                            preview_key = f"{file_path}preview/{file_name}"
                            s3_client.delete_object(Bucket='snapgoated', Key=preview_key)

                            deleted_count += 1
                            logger.info(f"ลบไฟล์: {key} และ {preview_key}")

                            if deleted_count % 100 == 0:
                                logger.info(f"ลบไฟล์ไปแล้ว {deleted_count} รายการ")

                        except Exception as e:
                            logger.error(f"เกิดข้อผิดพลาดในการลบไฟล์ {key}: {str(e)}")

        logger.info(
            f"การทำความสะอาดเสร็จสิ้น: ตรวจสอบไปแล้ว {checked_count} รายการ, ลบไปทั้งหมด {deleted_count} รายการ")
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

# ส่วนแรก: อัพเดทข้อมูลรูปภาพลงฐานข้อมูลทันที
async def save_images_to_database(images: list, event_id: int, user_id: int, db: Session):
    results = []

    for image in images:
        try:
            file_name = image.get("name")
            file_path = f"{user_id}/{event_id}/"

            # สร้าง record ในฐานข้อมูล
            new_photo = Photo(
                file_name=file_name,
                file_path=file_path,
                size=image.get("size", 0),
                is_detected_face=False,  # ยังไม่ได้ตรวจจับใบหน้า
            )

            db.add(new_photo)
            db.flush()  # ให้ได้ ID ก่อนที่จะ commit

            # เพิ่มความสัมพันธ์กับ event
            event_photo = EventPhoto(
                event_id=event_id,
                photo_id=new_photo.id
            )
            db.add(event_photo)

            # เก็บข้อมูลสำหรับการประมวลผลใบหน้าในภายหลัง
            results.append({
                "photo_id": new_photo.id,
                "file_name": file_name,
                "file_path": file_path
            })
        except Exception as e:
            logger.error(f"เกิดข้อผิดพลาดในการบันทึกรูปภาพ {file_name}: {str(e)}")

    # อัพเดทข้อมูล event
    event = db.query(Event).filter(Event.id == event_id).first()
    event.total_image_count += len(results)

    db.commit()

    return results

async def create_folder(websocket: WebSocket, event_id: int, current_user: User, db: Session, folder_name: str):
    folder_path = f"{current_user.id}/{event_id}"
    folder_name = check_duplicate_name(f"{folder_name}/", folder_path, True)
    full_path = f"{current_user.id}/{event_id}/{folder_name}"

    create_folder_in_spaces(full_path)
    event_folder = EventFolder(
        event_id=event_id,
        name=folder_name
    )
    try:
        db.add(event_folder)
        db.commit()
        db.refresh(event_folder)
    except Exception as e:
        db.rollback()
        await websocket.send_json({
            "message": f"Error creating folder: {str(e)}",
            "status": "error",
            "status_code": 500
        })
        await websocket.close(code=1011)
        return

    await websocket.send_json({
        "message": f"Folder {folder_name} created successfully",
        "status": "success",
        "status_code": 200,
        "data": {
            "folder_id": event_folder.id,
            "folder_name": event_folder.name
        }
    })

async def delete_file(websocket: WebSocket, event_id: int, file_id: int, db: Session, folder_id: Optional[int] = None):
    query = db.query(Photo).join(EventPhoto)
    if folder_id:
        query = query.join(EventFolderPhoto).filter(EventFolderPhoto.event_folder_id == folder_id)
    photo = query.filter(Photo.id == file_id, EventPhoto.event_id == event_id).first()

    if not photo:
        return await websocket.send_json({
            "message": "File not found",
            "status": "error",
            "status_code": 404,
            "data": {"file_id": file_id, "folder_id": folder_id}
        })

    delete_file_from_spaces(f"{photo.file_path}{photo.file_name}")
    delete_file_from_spaces(f"{photo.file_path}preview/{photo.file_name}")

    # Update event file size and count
    event = db.query(Event).filter(Event.id == event_id).first()
    event.total_image_count -= 1
    event.total_image_size -= photo.size
    db.commit()

    if folder_id:
        # Update event folder file size and count
        event_folder = db.query(EventFolder).filter(EventFolder.id == folder_id).first()
        if event_folder:
            event_folder.total_photo_count -= 1
            event_folder.total_photo_size -= photo.size
            db.commit()

    # Delete photo vectors
    db.query(PhotoFaceVector).filter(PhotoFaceVector.photo_id == photo.id).delete()
    db.commit()

    if folder_id:
        # Delete from EventFolderPhoto
        db.query(EventFolderPhoto).filter(EventFolderPhoto.photo_id == photo.id,
                                          EventFolderPhoto.event_folder_id == folder_id).delete()
    else:
        # Delete from EventPhoto
        db.query(EventPhoto).filter(EventPhoto.photo_id == photo.id).delete()
    db.commit()

    # Delete photo from database
    db.delete(photo)
    db.commit()

    await websocket.send_json({
        "message": f"File {photo.file_name} deleted successfully",
        "status": "success",
        "status_code": 200,
        "data": {"file_id": photo.id}
    })

async def delete_folder(websocket: WebSocket, event_id: int, folder_id: int, db: Session):
    event_folder = db.query(EventFolder).filter(
        EventFolder.id == folder_id,
        EventFolder.event_id == event_id
    ).first()
    if not event_folder:
        return await websocket.send_json({
            "message": "Folder not found",
            "status": "error",
            "status_code": 404,
            "data": {"folder_id": folder_id}
        })

    # Delete all photos in folder
    photos = db.query(Photo).join(EventFolderPhoto).filter(
        EventFolderPhoto.event_folder_id == folder_id
    ).all()
    for photo in photos:
        delete_file_from_spaces(f"{photo.file_path}{photo.file_name}")
        delete_file_from_spaces(f"{photo.file_path}preview/{photo.file_name}")

        # Update event file size and count
        event = db.query(Event).filter(Event.id == event_id).first()
        event.total_image_count -= 1
        event.total_image_size -= photo.size
        db.commit()

        # Delete photo vectors
        db.query(PhotoFaceVector).filter(PhotoFaceVector.photo_id == photo.id).delete()
        db.commit()

        # Delete from EventFolderPhoto
        db.query(EventFolderPhoto).filter(
            EventFolderPhoto.photo_id == photo.id,
            EventFolderPhoto.event_folder_id == folder_id
        ).delete()
        db.commit()

        # Delete photo from database
        db.delete(photo)
        db.commit()

    # Delete folder
    db.delete(event_folder)
    db.commit()

    await websocket.send_json({
        "message": f"Folder {event_folder.name} deleted successfully",
        "status": "success",
        "status_code": 200,
        "data": {"folder_id": event_folder.id}
    })

def insert_vector_to_db(photo_id: int, vector: np.ndarray) -> bool:
    """Insert single vector to database with its own session"""
    with closing(SessionLocal()) as db:
        try:
            vector_json = json.dumps(vector.tolist() if isinstance(vector, np.ndarray) else vector)
            insert_face_vector(db, photo_id, vector_json)
            db.commit()
            return True
        except Exception as e:
            db.rollback()
            logger.error(f"Error inserting vector for photo {photo_id}: {e}")
            return False

def sanitize_filename(filename: str) -> str:
    """
    ทำความสะอาดชื่อไฟล์เพื่อป้องกันปัญหาในการอัพโหลดและการเข้าถึง
    """
    if not filename:
        return "unnamed_file"

    # แยกนามสกุลไฟล์
    name_parts = filename.rsplit('.', 1)
    name = name_parts[0]
    extension = name_parts[1] if len(name_parts) > 1 else ""

    # แทนที่ช่องว่างด้วย underscore
    name = name.replace(' ', '_')

    # กรองอักขระพิเศษที่อาจมีปัญหา
    # เก็บเฉพาะตัวอักษร ตัวเล�� _ และ -
    import re
    name = re.sub(r'[^\w\-]', '', name)

    # จำกัดความยาวชื่อไฟล์
    max_length = 200  # รวมนามสกุล
    if len(name) + len(extension) + 1 > max_length:
        name = name[:max_length - len(extension) - 1]

    # รวมนามสกุลกลับเข้าไป
    if extension:
        return f"{name}.{extension}"
    return name
