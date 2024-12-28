from sqlalchemy import Column, Integer, String, Text, DateTime
from sqlalchemy.orm import relationship
from app.db.base import Base
from datetime import datetime

class Image(Base):
    __tablename__ = 'images'

    id = Column(Integer, primary_key=True, autoincrement=True)
    filename = Column(String(255), nullable=False)
    preview_url = Column(Text, nullable=False)
    download_url = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationship with ImageVector
    vectors = relationship('ImageVector', back_populates='image', cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Image(id={self.id}, filename={self.filename})>"