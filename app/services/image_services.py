import time

import numpy as np
from typing import List

from fastapi import UploadFile, File, HTTPException
from sqlalchemy.exc import OperationalError

from app.schemas.user import Response
from app.services.digital_oceans import generate_presigned_url
# from app.services.s3_service import upload_to_s3, generate_presigned_url
from app.utils.model.face_detect import process_image_main_face, process_image_faces
from app.db.queries.image_queries import insert_face_vector, get_images_with_vectors
# from app.services.s3_service import upload_to_s3, generate_presigned_url
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

# @retry_on_exception(OperationalError, retries=3, delay=2)
# async def save_image_and_vectors(files: List, db: Session):
#     saved_files = []
#     for file in files:
#         try:
#             vectors = await process_image_faces(file)
#
#             isUploaded = upload_to_s3(file, file.filename)
#             if not isUploaded:
#                 saved_files.append({"file_name": file.filename, "status": "failed"})
#                 continue
#
#             image_id = insert_image(db, file.filename)
#
#             if vectors is not None:
#                 for vector in vectors:
#                     if isinstance(vector, np.ndarray):
#                         vector = vector.tolist()
#                     insert_face_vector(db, image_id, json.dumps(vector))
#
#             saved_files.append({"file_name": file.filename, "status": "success"})
#
#         except Exception as e:
#             print(f"Error processing file: {file.filename}")
#             print(f"Error message: {str(e)}")
#             traceback_lines = traceback.format_exc().splitlines()
#             error_line = traceback_lines[-2]
#             print(f"Error occurred at: {error_line}")
#             saved_files.append({"file_name": file.filename, "message": e, "status": "failed"})
#             continue
#
#     return {"saved_files": saved_files}

# async def detected_faces(file: UploadFile = File(...)):
#     try:
#         vectors = await process_image_faces(file)
#         if vectors is None:
#             raise HTTPException(status_code=400, detail="No faces detected in the image")
#         return {"vectors": vectors}
#     except Exception as e:
#         raise HTTPException(status_code=500, detail=str(e))
#
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
                    "filename": record.photo.filename.split('/')[-1],
                    "preview_url": generate_presigned_url(
                        f"{record.photo.filename.rsplit('/', 1)[0]}/preview_{record.photo.filename.split('/')[-1]}"),
                    "download_url": generate_presigned_url(record.photo.filename)
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
