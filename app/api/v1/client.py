import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, BackgroundTasks, File
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session
from typing import List, Dict, Any, Optional

from app.db.models import EventType, City
from app.db.session import get_db
from app.db.models.Event import Event
from app.schemas.user import Response
from app.services.digital_oceans import generate_presigned_url
from app.services.image_services import find_similar_faces

public_router = APIRouter()

@public_router.get("/public-events", response_model=Response)
def get_public_events(
    page: int = 1,
    limit: int = 10,
    search: Optional[str] = None,
    event_type_id: Optional[int] = None,
    city_id: Optional[int] = None,
    date: Optional[str] = None,
    db: Session = Depends(get_db)
):
    if page < 1:
        return Response(
            message="Page number must be greater than 0",
            status_code=400,
            status="error"
        )

    query = db.query(Event).filter(Event.status == True)

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

    total_events = query.count()
    skip = (page - 1) * limit
    events = query.order_by(Event.date).offset(skip).limit(limit).all()
    total_pages = (total_events + limit - 1) // limit

    events_data = [
        {
            "id": event.id,
            "event_name": event.event_name,
            "event_type_id": event.event_type_id,
            "date": event.date,
            "location": event.location,
            "status": event.status,
            "user_id": event.user_id,
            "publish_at": event.publish_at,
            "cover_url": generate_presigned_url(event.cover_photo.file_name) if event.cover_photo else None
        }
        for event in events
    ]

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
            f"{event.cover_photo.file_path}/preview_{event.cover_photo.file_name}"
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
        # Log start of processing
        print(f"Starting image search for event {event_id}")

        # Validate event
        event = db.query(Event).filter(Event.id == event_id, Event.status == True).first()
        if not event:
            print(f"Event {event_id} not found")
            raise HTTPException(status_code=404, detail="Event not found")

        # Read file content with size limit
        MAX_SIZE = 10 * 1024 * 1024  # 10MB
        file_content = b''
        total_size = 0

        while chunk := await file.read(1024 * 1024):  # 1MB chunks
            total_size += len(chunk)
            if total_size > MAX_SIZE:
                print("File size exceeds limit")
                raise HTTPException(status_code=413, detail="File too large")
            file_content += chunk

        if not file_content:
            print("Empty file received")
            raise HTTPException(status_code=400, detail="Empty file")

        # Reset file position
        await file.seek(0)

        # Process with timeout
        print("Starting face detection")
        try:
            response = await asyncio.wait_for(
                find_similar_faces(event_id, file, db),
                timeout=180  # 3 minutes timeout
            )
            print("Face detection completed successfully")
            return response

        except asyncio.TimeoutError:
            print("Processing timeout")
            raise HTTPException(status_code=504, detail="Processing timeout")

    except HTTPException as he:
        raise he
    except Exception as e:
        print(f"Unexpected error: {str(e)}")
        import traceback
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail="Processing failed")