# app/db/models/EventType.py
from sqlalchemy import Column, Integer, String, Text, DateTime
from sqlalchemy.orm import relationship
from app.db.base import Base
from datetime import datetime

class EventType(Base):
    __tablename__ = 'event_types'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), unique=True, nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=True)
    name_en = Column(String(255), default='', nullable=False)
    name_th = Column(String(255), default='', nullable=False)

    events = relationship("Event", back_populates="event_type")

    def __repr__(self):
        return f"<EventType(id={self.id}, name={self.name})>"