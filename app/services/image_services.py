import time

import numpy as np
from typing import List

from sqlalchemy.exc import OperationalError

from app.utils.model.face_detect import process_image_main_face, process_image_faces
from app.db.queries.image_queries import insert_image, insert_face_vector, get_images_with_vectors
from app.services.s3_service import upload_to_s3, generate_presigned_url
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

@retry_on_exception(OperationalError, retries=3, delay=2)
async def save_image_and_vectors(files: List, db: Session):
    saved_files = []
    for file in files:
        try:
            vectors = await process_image_faces(file)

            isUploaded = upload_to_s3(file, file.filename)
            if not isUploaded:
                saved_files.append({"file_name": file.filename, "status": "failed"})
                continue

            image_id = insert_image(db, file.filename)

            if vectors is not None:
                for vector in vectors:
                    if isinstance(vector, np.ndarray):
                        vector = vector.tolist()
                    insert_face_vector(db, image_id, json.dumps(vector))

            saved_files.append({"file_name": file.filename, "status": "success"})
        
        except Exception as e:
            print(f"Error processing file: {file.filename}")
            print(f"Error message: {str(e)}")
            traceback_lines = traceback.format_exc().splitlines()
            error_line = traceback_lines[-2]
            print(f"Error occurred at: {error_line}")
            saved_files.append({"file_name": file.filename, "message": e, "status": "failed"})
            continue

    return {"saved_files": saved_files}

async def find_similar_faces(file, db: Session):
    try:
        print("Processing image:", file.filename)
        threshold = 0.94

        query_vector = await process_image_main_face(file)  # Assume main face
        results = get_images_with_vectors(db)
        similar_faces = []

        for record in results:
            vector = np.array(json.loads(record.vector), dtype=np.float32)
            query_vector = np.ravel(query_vector)
            vector = np.ravel(vector)
            similarity = 1 - cosine(query_vector, vector)
            if similarity >= threshold:
                similar_faces.append({
                    "id": record.id,
                    "file_path": generate_presigned_url(record.image.filename),
                    "filename": record.image.filename,
                })

        filtered_faces = {}
        matches_faces = []
        for face in similar_faces:
            file_path = face["file_path"]
            if file_path not in filtered_faces:
                filtered_faces[file_path] = face
                matches_faces.append(face)
    except Exception as e:
        print(f"Error processing file: {file.filename}")
        print(f"Error message: {str(e)}")
        traceback_lines = traceback.format_exc().splitlines()
        error_line = traceback_lines[-2]
        print(f"Error occurred at: {error_line}")

    return {
            "message": "Matching images found",
            "matches": matches_faces
            }

    #Dlib
    # faces = face_detector(rgb_image)
    # # Assume the largest face is the main face
    # main_face = max(faces, key=lambda rect: rect.width() * rect.height())
    # shape = shape_predictor(rgb_image, main_face)
    # query_vector = np.array(face_rec_model.compute_face_descriptor(rgb_image, shape))

    #MTCN
    # faces = detector.detect_faces(rgb_image)
    # for face in faces:
    #     x, y, w, h = face['box']
    #     rect = dlib.rectangle(x, y, x+w, y+h)
    #     shape = shape_predictor(image, rect)
    #     query_vector = np.array(face_rec_model.compute_face_descriptor(image, shape))
