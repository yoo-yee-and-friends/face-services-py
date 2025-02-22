import time

import numpy as np

from app.schemas.user import Response
from app.services.digital_oceans import generate_presigned_url
from app.utils.model.face_detect import process_image_main_face
from app.db.queries.image_queries import get_images_with_vectors
from sqlalchemy.orm import Session
import json
import traceback
from scipy.spatial.distance import cosine

def retry_on_exception(exception, retries=3, delay=2):
    def decorator(func):
        def wrapper(*args, **kwargs):
            attempts = 0
            while attempts < retries:
                try:
                    return func(*args, **kwargs)
                except exception as e:
                    attempts += 1
                    if attempts == retries:
                        raise
                    time.sleep(delay)
        return wrapper
    return decorator

async def find_similar_faces(event_id, file, db: Session):
    matches_faces = []
    try:
        print("Processing image:", file.filename)
        threshold = 0.94

        query_vector = await process_image_main_face(file)  # Assume main face
        results = get_images_with_vectors(db, event_id)

        for record in results:
            vector = np.array(json.loads(record.vector), dtype=np.float32)
            query_vector = np.ravel(query_vector)
            vector = np.ravel(vector)
            similarity = 1 - cosine(query_vector, vector)
            if similarity >= threshold:
                matches_faces.append({
                    "id": record.id,
                    "similarity": similarity,
                    "file_name": record.photo.file_name.split('/')[-1],
                    "preview_url": generate_presigned_url(
                        f"{record.photo.file_name.rsplit('/', 1)[0]}/preview_{record.photo.file_name.split('/')[-1]}"),
                    "download_url": generate_presigned_url(record.photo.file_name)
                })

        matches_faces = sorted(matches_faces, key=lambda x: x['similarity'], reverse=True)
    except Exception as e:
        print(f"Error processing file: {file.filename}")
        print(f"Error message: {str(e)}")
        traceback_lines = traceback.format_exc().splitlines()
        error_line = traceback_lines[-2]
        print(f"Error occurred at: {error_line}")

    message = "Matching images found" if matches_faces else "No matching images found"
    return Response(
        message=message,
        status_code=200,
        status="Success",
        data={
            "matches": matches_faces
        }
    )
