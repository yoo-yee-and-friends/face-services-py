# app/db/models/City.py
from sqlalchemy import Column, Integer, String, DateTime, Numeric, ForeignKey
from sqlalchemy.orm import relationship
from app.db.base import Base
from datetime import datetime
from app.db.models import *

class City(Base):
    __tablename__ = 'cities'

    id = Column(Integer, primary_key=True, autoincrement=True)
    country_id = Column(Integer, ForeignKey('countries.id', ondelete='CASCADE'), nullable=False)
    name_en = Column(String(100), nullable=False)
    name_th = Column(String(100), nullable=True)
    state = Column(String(100), nullable=True)
    latitude = Column(Numeric(10, 6), nullable=True)
    longitude = Column(Numeric(10, 6), nullable=True)
    population = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=True)

    events = relationship("Event", back_populates="city")

    def __repr__(self):
        return f"<City(id={self.id}, name={self.name_en})>"