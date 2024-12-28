from sqlalchemy import Column, Integer, String, Text, ForeignKey, DateTime, JSON
from sqlalchemy.orm import relationship
from app.db.base import Base
from sqlalchemy.dialects.postgresql import JSON
from datetime import datetime

class ImageVector(Base):
    __tablename__ = 'image_vectors'

    id = Column(Integer, primary_key=True, autoincrement=True)
    image_id = Column(Integer, ForeignKey('images.id', ondelete='CASCADE'), nullable=False)
    vector = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationship with Image
    image = relationship('Image', back_populates='vectors')

    def __repr__(self):
        return f"<ImageVector(id={self.id}, image_id={self.image_id})>"