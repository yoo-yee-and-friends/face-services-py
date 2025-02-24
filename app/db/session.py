from sqlalchemy import create_engine, QueuePool
from sqlalchemy.orm import sessionmaker
from app.config.settings import settings

engine = create_engine(
    settings.DATABASE_URL,
    poolclass=QueuePool,
    pool_size=5,  # จำนวน connections ที่สร้างไว้
    max_overflow=10,  # จำนวน connections เพิ่มเติมที่ยอมให้สร้างได้
    pool_timeout=30,  # timeout สำหรับการรอ connection
    pool_recycle=1800  # recycle connection ทุก 30 นาที
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
