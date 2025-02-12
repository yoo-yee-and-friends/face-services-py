# app/db/models/EventFolder.py
from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, func
from sqlalchemy.orm import relationship
from app.db.base import Base
from app.db.models.EventFolderPhoto import EventFolderPhoto

class EventFolder(Base):
    __tablename__ = 'event_folders'

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(Integer, ForeignKey('events.id', ondelete='CASCADE'), nullable=True)
    name = Column(String(255), nullable=False)
    total_photo_count = Column(Integer, default=0, nullable=False)
    total_photo_size = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, server_default=func.current_timestamp(), nullable=False)
    updated_at = Column(DateTime, server_default=func.current_timestamp(), onupdate=func.current_timestamp(), nullable=False)

    event = relationship("Event", back_populates="folders")
    photos = relationship("EventFolderPhoto", back_populates="event_folder")