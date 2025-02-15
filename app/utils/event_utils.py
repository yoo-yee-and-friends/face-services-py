from typing import Optional, List

from sqlalchemy import desc
from sqlalchemy.orm import Session
from app.db.models.Event import Event
from app.services.digital_oceans import generate_presigned_url

def get_event_query(db: Session, current_user, status: Optional[bool], search: Optional[str]):
    query = db.query(Event).filter(Event.user_id == current_user.id)
    if status is not None:
        query = query.filter(Event.status == status)
    if search:
        query = query.filter(
            (Event.event_name.ilike(f"%{search}%")) |
            (Event.location.ilike(f"%{search}%"))
        )
    query = query.order_by(desc(Event.updated_at))
    return query

def paginate_query(query, page: int, limit: int):
    skip = (page - 1) * limit
    return query.order_by(Event.date).offset(skip).limit(limit).all()

def format_event_data(events: List[Event]):
    return [
        {
            "id": event.id,
            "event_name": event.event_name,
            "event_type_id": event.event_type_id,
            "date": event.date,
            "location": event.location,
            "status": event.status,
            "user_id": event.user_id,
            "publish_at": event.publish_at,
            "cover_url": generate_presigned_url(f"{event.cover_photo.file_path}{event.cover_photo.file_name}") if event.cover_photo else None
        }
        for event in events
    ]