import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, BackgroundTasks, File
from fastapi.encoders import jsonable_encoder
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload
from typing import List, Dict, Any, Optional
import io
import os
from PIL import Image
from app.db.models import EventType, City
from app.db.models.Photo import Photo
from app.db.session import get_db
from app.db.models.Event import Event
from app.schemas.user import Response
from app.services.digital_oceans import generate_presigned_url
from app.services.image_services import find_similar_faces
from pillow_heif import register_heif_opener

public_router = APIRouter()
register_heif_opener()


@public_router.get("/public-events", response_model=Response)
async def get_public_events(
        page: int = 1,
        limit: int = 10,
        search: Optional[str] = None,
        event_type_id: Optional[int] = None,
        city_id: Optional[int] = None,
        date: Optional[str] = None,
        db: Session = Depends(get_db)
):
    # Base query without eager loading
    query = db.query(Event).filter(Event.status == True)

    # Apply filters
    if search:
        query = query.filter(
            (Event.event_name.ilike(f"%{search}%")) |
            (Event.location.ilike(f"%{search}%"))
        )
    if event_type_id:
        query = query.filter(Event.event_type_id == event_type_id)
    if city_id:
        query = query.filter(Event.city_id == city_id)
    if date:
        query = query.filter(Event.date == date)

    # Get counts and pagination
    total_events = db.scalar(query.with_entities(func.count(Event.id)))
    total_pages = (total_events + limit - 1) // limit
    skip = (page - 1) * limit

    # Get basic event data only
    events = query.order_by(Event.date).offset(skip).limit(limit).all()

    # Process results with minimum data
    events_data = []
    for event in events:
        data = {
            "id": event.id,
            "event_name": event.event_name,
            "date": event.date,
            "location": event.location,
            "event_type_id": event.event_type_id,
            "cover_url": None  # จะเติมภายหลัง
        }

        # ดึง cover URL เฉพาะถ้ามี cover_photo_id
        if event.cover_photo_id:
            cover_photo = db.query(Event.cover_photo_id).filter(
                Event.id == event.id
            ).first()
            if cover_photo and cover_photo[0]:
                photo = db.query(Photo).filter(Photo.id == event.cover_photo_id).first()
                if photo:
                    data["cover_url"] = generate_presigned_url(
                        f"{photo.file_path}{photo.file_name}"
                    )

        events_data.append(data)

    return Response(
        message="Events fetched successfully",
        status_code=200,
        status="success",
        data={
            "total_events": total_events,
            "total_pages": total_pages,
            "current_page": page,
            "events_per_page": limit,
            "events": events_data
        }
    )

@public_router.get("/public-event-data", response_model=Response)
def get_public_event_data(
    db: Session = Depends(get_db)
):
    event_types = jsonable_encoder(db.query(EventType.EventType).all())
    cities = jsonable_encoder(db.query(City.City).all())

    return Response(
        message="Data retrieved successfully",
        data={
            "event_types": event_types,
            "cities": cities
        },
        status="success",
        status_code=200
    )

@public_router.get("/public-event", response_model=Response)
def get_public_event(
    event_id: int,
    db: Session = Depends(get_db)
):
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    event_data = {
        "event_id": event.id,
        "event_name": event.event_name,
        "event_type": event.event_type.name,
        "event_cover_photo": generate_presigned_url(
            f"{event.cover_photo.file_path}{event.cover_photo.file_name}"
        ),
        "date": event.date
    }

    return Response(
        message="Event retrieved successfully",
        data=event_data,
        status="success",
        status_code=200
    )

@public_router.post("/search-image", response_model=Response)
async def search_image(
        event_id: int,
        file: UploadFile = File(...),
        db: Session = Depends(get_db)
):
    try:
        # อ่านข้อมูลไฟล์
        contents = await file.read()

        # ตรวจสอบว่าเป็นไฟล์ HEIC หรือไม่
        is_heic = file.filename.lower().endswith('.heic') or file.content_type == 'image/heic'

        if is_heic:
            # แปลง HEIC เป็น JPEG
            with io.BytesIO(contents) as heic_io:
                try:
                    # เปิดไฟล์ HEIC
                    image = Image.open(heic_io)

                    # แปลงเป็น JPEG และเก็บในหน่วยความจำ
                    output_io = io.BytesIO()
                    image.convert('RGB').save(output_io, format='JPEG')
                    output_io.seek(0)

                    # สร้าง UploadFile ใหม่
                    new_filename = os.path.splitext(file.filename)[0] + ".jpg"
                    converted_file = UploadFile(
                        filename=new_filename,
                        file=output_io,
                        content_type="image/jpeg"
                    )

                    # ใช้ไฟล์ที่แปลงแล้วแทน
                    print(f"แปลงไฟล์ HEIC เป็น JPEG: {file.filename} -> {new_filename}")
                    file = converted_file
                except Exception as e:
                    raise HTTPException(
                        status_code=400,
                        detail=f"ไม่สามารถแปลงไฟล์ HEIC ได้: {str(e)}"
                    )

        # ดำเนินการค้นหาใบหน้าด้วยไฟล์ที่แปลงแล้ว
        await file.seek(0)  # รีเซ็ตตำแหน่งการอ่านไฟล์
        response = await find_similar_faces(event_id, file, db)
        return response
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"เกิดข้อผิดพลาดในการค้นหาภาพ: {str(e)}")
