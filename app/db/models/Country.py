# app/db/models/Country.py
from sqlalchemy import Column, Integer, String, DateTime, Text, BigInteger
from sqlalchemy.orm import relationship
from app.db.base import Base
from datetime import datetime

class Country(Base):
    __tablename__ = 'countries'

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(2), unique=True, nullable=False)
    code3 = Column(String(3), unique=True, nullable=False)
    name_en = Column(String(100), default='', nullable=False)
    name_th = Column(String(100), default='', nullable=False)
    phone_code = Column(String(10), default='', nullable=False)
    currency_code = Column(String(3), default='', nullable=False)
    time_zone = Column(Text, default='', nullable=False)
    continent = Column(String(50), default='', nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=True)

    events = relationship("Event", back_populates="country")

    def __repr__(self):
        return f"<Country(id={self.id}, name={self.name_en})>"