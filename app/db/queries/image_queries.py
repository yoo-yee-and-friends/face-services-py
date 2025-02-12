from fastapi import HTTPException
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.db.models.EventFolderPhoto import EventFolderPhoto
from app.db.models.EventPhoto import EventPhoto
from app.db.models.Photo import Photo
from app.db.models.PhotoVector import PhotoVector

# def insert_image(db: Session, filename: str):
#     image = Image(filename=filename)
#     db.add(image)
#     db.commit()
#     db.refresh(image)
#     return image.id

def insert_face_vector(db: Session, image_id: int, vector: list):
    face_vector = PhotoVector(photo_id=image_id, vector=vector)
    db.add(face_vector)
    db.commit()

def get_images_with_vectors(db: Session, event_id: int):
    try:
        return db.query(PhotoVector).join(Photo).filter(
            Photo.is_detected_face == True,
            Photo.id.in_(
                db.query(EventPhoto.photo_id).filter(EventPhoto.event_id == event_id).union(
                    db.query(EventFolderPhoto.photo_id).filter(EventFolderPhoto.event_folder_id == event_id)
                )
            )
        ).all()
    except OperationalError as e:
        raise HTTPException(status_code=500, detail="Database connection error: " + str(e))
