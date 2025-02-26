# app/db/models/Photo.py
from sqlalchemy import Column, Integer, String, Text, DateTime, BigInteger, JSON, Boolean
from sqlalchemy.orm import relationship
from app.db.base import Base
from datetime import datetime
from app.db.models.User import User
from app.db.models.PhotoVector import PhotoVector
from app.db.models.PhotoFaceVector import PhotoFaceVector



class Photo(Base):
    __tablename__ = 'photos'

    id = Column(Integer, primary_key=True, autoincrement=True)
    file_name = Column(String(255), nullable=False)
    uploaded_at = Column(DateTime, default=datetime.utcnow, nullable=True)
    photo_metadata = Column(JSON, nullable=True)
    description = Column(Text, nullable=True)
    file_path = Column(Text, nullable=False)
    size = Column(BigInteger, default=0, nullable=False)
    is_detected_face = Column(Boolean, default=False, nullable=False)

    user = relationship("User", back_populates="profile_photo")
    event = relationship("Event", back_populates="cover_photo", uselist=False)
    event_folder_photos = relationship("EventFolderPhoto", back_populates="photo")
    event_photos = relationship("EventPhoto", back_populates="photo")
    vectors = relationship("PhotoVector", back_populates="photo")
    face_vectors = relationship("PhotoFaceVector", back_populates="photo")

    def __repr__(self):
        return f"<Photo(id={self.id}, filename={self.filename})>"
