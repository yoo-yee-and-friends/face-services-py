from sqlalchemy import Column, Integer, String, Text, ForeignKey, DateTime, JSON
from sqlalchemy.orm import relationship
from app.db.base import Base
from sqlalchemy.dialects.postgresql import JSON
from datetime import datetime
from app.db.models import *

class PhotoVector(Base):
    __tablename__ = 'photo_vectors'

    id = Column(Integer, primary_key=True, autoincrement=True)
    photo_id = Column(Integer, ForeignKey('photos.id', ondelete='CASCADE'), nullable=False)
    vector = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=True)

    # Relationship with Image
    photo = relationship('Photo', back_populates='vectors')

    def __repr__(self):
        return f"<PhotoVector(id={self.id}, photo_id={self.photo_id})>"