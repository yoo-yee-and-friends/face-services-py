import string
from datetime import datetime
from random import random
from sqlalchemy import Column, Integer, DateTime, String
from app.db.base import Base
from sqlalchemy.testing import db
from app.db.models import *

class VerificationCode(Base):
    __tablename__ = 'verification_codes'

    id = Column(Integer, primary_key=True)
    code = Column(String(6), nullable=False)
    expired_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    purpose = Column(String(50), nullable=False)
    email = Column(String(255), nullable=False)
