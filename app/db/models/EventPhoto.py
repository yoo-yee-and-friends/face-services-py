# app/db/models/EventPhoto.py
from sqlalchemy import Column, Integer, ForeignKey
from sqlalchemy.orm import relationship
from app.db.base import Base

class EventPhoto(Base):
    __tablename__ = 'event_photos'

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(Integer, ForeignKey('events.id', ondelete='CASCADE'), nullable=True)
    photo_id = Column(Integer, ForeignKey('photos.id', ondelete='CASCADE'), nullable=True)

    event = relationship("Event", back_populates="event_photo")
    photo = relationship("Photo", back_populates="event_photos")