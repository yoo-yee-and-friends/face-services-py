# app/db/models/EventFolderPhoto.py
from sqlalchemy import Column, Integer, ForeignKey
from sqlalchemy.orm import relationship
from app.db.base import Base
from app.db.models import *

class EventFolderPhoto(Base):
    __tablename__ = 'event_folder_photos'

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_folder_id = Column(Integer, ForeignKey('event_folders.id', ondelete='CASCADE'), nullable=True)
    photo_id = Column(Integer, ForeignKey('photos.id', ondelete='CASCADE'), nullable=True)

    event_folder = relationship("EventFolder", back_populates="photos")
    photo = relationship("Photo", back_populates="event_folder_photos")

    def __repr__(self):
        return f"<EventFolderPhoto(id={self.id}, event_folder_id={self.event_folder_id}, photo_id={self.photo_id})>"

