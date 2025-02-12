# app/db/models/Event.py
from sqlalchemy import Column, Integer, DateTime, Text, Boolean, String, BigInteger, ForeignKey
from sqlalchemy.orm import relationship
from app.db.base import Base
from datetime import datetime
from app.db.models.EventType import EventType
from app.db.models.Country import Country
from app.db.models.EventFolder import EventFolder
from app.db.models.Photo import Photo
from app.db.models.EventPhoto import EventPhoto

class Event(Base):
    __tablename__ = 'events'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='SET NULL'), nullable=False)
    event_name = Column(String(255), nullable=False)
    date = Column(DateTime, nullable=False)
    location = Column(String(255), nullable=False)
    status = Column(Boolean, default=True, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    cover_photo_id = Column(Integer, ForeignKey('photos.id'), nullable=True)
    event_type_id = Column(Integer, ForeignKey('event_types.id', ondelete='CASCADE'), nullable=False)
    country_id = Column(Integer, ForeignKey('countries.id', ondelete='CASCADE'), nullable=False)
    city_id = Column(Integer, ForeignKey('cities.id', ondelete='CASCADE'), nullable=False)
    total_image_size = Column(BigInteger, default=0, nullable=False)
    total_image_count = Column(Integer, default=0, nullable=False)
    publish_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="events")
    country = relationship("Country", back_populates="events", lazy="joined")
    city = relationship("City", back_populates="events")
    event_type = relationship("EventType", back_populates="events")
    folders = relationship("EventFolder", back_populates="event")
    event_photo = relationship("EventPhoto", back_populates="event")
    cover_photo = relationship("Photo", back_populates="event", uselist=False, lazy="joined")

    def __repr__(self):
        return f"<Event(id={self.id}, name={self.event_name})>"


    @property
    def photos(self):
        from app.db.models.EventPhoto import EventPhoto
        return self.photos
