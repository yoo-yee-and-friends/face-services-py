from fastapi import HTTPException
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from app.db.models.Image import Image
from app.db.models.ImageVector import ImageVector

def insert_image(db: Session, filename: str):
    image = Image(filename=filename)
    db.add(image)
    db.commit()
    db.refresh(image)
    return image.id

def insert_face_vector(db: Session, image_id: int, vector: list):
    face_vector = ImageVector(image_id=image_id, vector=vector)
    db.add(face_vector)
    db.commit()

def get_images_with_vectors(db: Session):
    try:
        return db.query(ImageVector).all()
    except OperationalError as e:
        raise HTTPException(status_code=500, detail="Database connection error: " + str(e))
