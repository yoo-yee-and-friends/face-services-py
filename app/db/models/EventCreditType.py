from sqlalchemy import Column, Integer, String, Text, DateTime
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.base import Base

class EventCreditType(Base):
    __tablename__ = 'event_credit_types'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name_en = Column(String(50), nullable=False)
    name_th = Column(String(50), nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.current_timestamp(), nullable=True)

    event_credits = relationship("EventCredit", back_populates="event_credit_type")