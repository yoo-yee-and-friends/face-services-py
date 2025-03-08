from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, Integer, ForeignKey, DateTime

from sqlalchemy.orm import relationship
from app.db.base import Base

from datetime import datetime
from app.db.models import *

class PhotoFaceVector(Base):
    __tablename__ = 'photo_face_vectors'

    id = Column(Integer, primary_key=True, autoincrement=True)
    photo_id = Column(Integer, ForeignKey('photos.id', ondelete='CASCADE'), nullable=False)
    vector = Column(Vector(512), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=True)

    photo = relationship('Photo', back_populates='face_vectors')

    def __repr__(self):
        return f"<PhotoVector(id={self.id}, photo_id={self.photo_id})>"