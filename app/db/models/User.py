# app/db/models/User.py
from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, Boolean
from sqlalchemy.orm import relationship
from app.db.base import Base
from app.db.models.Role import Role

class User(Base):
    __tablename__ = 'users'

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    role_id = Column(Integer, ForeignKey('roles.id', ondelete='SET NULL'), nullable=True)
    created_at = Column(DateTime, nullable=True)
    display_name = Column(String(255), nullable=False)
    email = Column(String(255), unique=True, nullable=False)
    email_verified = Column(Boolean, default=False, nullable=True)
    agree_policy = Column(Boolean, default=False, nullable=False)
    sync_google = Column(Boolean, default=False, nullable=True)
    profile_photo_id = Column(Integer, ForeignKey('photos.id', ondelete='SET NULL'), nullable=True)

    role = relationship("Role", back_populates="users")
    profile_photo = relationship("Photo", back_populates="user", uselist=False)
    events = relationship("Event", back_populates="user")

    def __repr__(self):
        return f"<User(id={self.id}, username={self.username})>"
