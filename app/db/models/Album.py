from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Float
from sqlalchemy.orm import relationship
from app.db.base import Base
from datetime import datetime

class Album(Base):
    __tablename__ = 'albums'

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True, nullable=False)
    creator_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    file_count = Column(Integer, default=0)
    total_size = Column(Float, default=0.0)
    creator = relationship("User", back_populates="albums")