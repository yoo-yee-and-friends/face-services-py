from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session
from typing import List, Dict, Any, Optional
from app.db.session import get_db
from app.db.models.Event import Event
from app.services.digital_oceans import generate_presigned_url
from app.services.image_services import find_similar_faces

public_router = APIRouter()

@public_router.get("/public-events", response_model=Dict[str, Any])
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
        raise HTTPException(status_code=400, detail="Page number must be greater than 0")

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
            "cover_url": generate_presigned_url(event.cover_photo.filename) if event.cover_photo else None
        }
        for event in events
    ]

    return {
        "total_events": total_events,
        "total_pages": total_pages,
        "current_page": page,
        "events_per_page": limit,
        "events": events_data
    }

@public_router.post("/search-image")
async def search_image(
        event_id: int,
        file: UploadFile,
        db: Session = Depends(get_db)
    ):
    try:
        event = db.query(Event).filter(Event.id == event_id, Event.status == True).first()
        if not event:
            raise HTTPException(status_code=404, detail="Event not found")
        print("Searching for similar faces")
        response = await find_similar_faces(event_id, file, db)
        return response
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))