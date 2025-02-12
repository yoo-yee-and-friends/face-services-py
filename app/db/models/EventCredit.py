from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.base import Base
from app.db.models.EventCreditType import EventCreditType

class EventCredit(Base):
    __tablename__ = 'event_credits'

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(Integer, ForeignKey('events.id', ondelete='CASCADE'), nullable=False)
    event_credit_type_id = Column(Integer, ForeignKey('event_credit_types.id', ondelete='CASCADE'), nullable=False)
    name = Column(String(255), nullable=False)
    created_at = Column(DateTime, server_default=func.current_timestamp(), nullable=True)

    event_credit_type = relationship("EventCreditType", back_populates="event_credits")

    def __repr__(self):
        return f"<EventCredit(id={self.id}, name={self.name})>"