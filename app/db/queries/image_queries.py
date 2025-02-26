import json

import numpy as np
from fastapi import HTTPException
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.db.models.EventFolderPhoto import EventFolderPhoto
from app.db.models.EventPhoto import EventPhoto
from app.db.models.Photo import Photo
from app.db.models.PhotoFaceVector import PhotoFaceVector


def insert_face_vector(db: Session, photo_id: int, vector_data: str):
    """Insert face vector data into PhotoFaceVector table"""
    vector_array = np.array(json.loads(vector_data), dtype=np.float32)
    if vector_array.size != 128:
        raise ValueError("Vector must be 128-dimensional")

    face_vector = PhotoFaceVector(
        photo_id=photo_id,
        vector=vector_array
    )
    db.add(face_vector)
    return face_vector

def get_images_with_vectors(db: Session, event_id: int):
    try:
        return db.query(PhotoFaceVector).join(Photo).filter(
            Photo.is_detected_face == True,
            Photo.id.in_(
                db.query(EventPhoto.photo_id).filter(EventPhoto.event_id == event_id).union(
                    db.query(EventFolderPhoto.photo_id).filter(EventFolderPhoto.event_folder_id == event_id)
                )
            )
        ).all()
    except OperationalError as e:
        raise HTTPException(status_code=500, detail="Database connection error: " + str(e))
