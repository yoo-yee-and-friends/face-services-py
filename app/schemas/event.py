from typing import List, Optional

from pydantic import BaseModel
from datetime import datetime

class Event(BaseModel):
    id: int
    user_id: int
    event_name: str
    date: datetime
    location: str
    status: bool
    created_at: datetime
    updated_at: datetime
    cover_photo_id: int
    event_type_id: int
    country_id: int
    city_id: int
    total_image_size: int
    total_image_count: int
    publish_at: datetime

    class Config:
        from_attributes = True

class Credit(BaseModel):
    credit_type_id: str
    name: str

class EventCreate(BaseModel):
    event_name: str
    event_type: str
    date: str
    location_name: str
    country_id: int
    city_id: int
    status: Optional[bool] = False
    credits: Optional[List[Credit]] = []