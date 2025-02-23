import time
from functools import lru_cache
from typing import List, Dict

import numpy as np

from app.schemas.user import Response
from app.services.digital_oceans import generate_presigned_url
from app.utils.model.face_detect import process_image_main_face
from app.db.queries.image_queries import get_images_with_vectors
from sqlalchemy.orm import Session
import json
import traceback
from scipy.spatial.distance import cosine

BATCH_SIZE = 100
THRESHOLD = 0.94
CACHE_SIZE = 128


@lru_cache(maxsize=CACHE_SIZE)
def calculate_similarity(query_vector_tuple: tuple, stored_vector_tuple: tuple) -> float:
    query_vector = np.array(query_vector_tuple)
    stored_vector = np.array(stored_vector_tuple)
    return 1 - cosine(query_vector, stored_vector)


async def process_batch(query_vector: np.ndarray, batch: List[Dict], threshold: float = THRESHOLD) -> List[Dict]:
    matches = []
    query_vector = np.ravel(query_vector)

    for record in batch:
        vector = np.array(json.loads(record.vector), dtype=np.float32)
        vector = np.ravel(vector)

        # Convert numpy arrays to tuples for caching
        similarity = calculate_similarity(tuple(query_vector), tuple(vector))

        if similarity >= threshold:
            matches.append({
                "id": record.id,
                "similarity": float(similarity),
                "file_name": record.photo.file_name.split('/')[-1],
                "preview_url": generate_presigned_url(
                    f"{record.photo.file_name.rsplit('/', 1)[0]}/preview_{record.photo.file_name.split('/')[-1]}"),
                "download_url": generate_presigned_url(record.photo.file_name)
            })
    return matches

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


async def find_similar_faces(event_id: int, file, db: Session):
    matches_faces = []
    try:
        print("Processing image:", file.filename)
        query_vector = await process_image_main_face(file)

        # Get total count of images
        all_results = get_images_with_vectors(db, event_id)

        # Process in batches
        batch = []
        for record in all_results:
            batch.append(record)
            if len(batch) >= BATCH_SIZE:
                batch_matches = await process_batch(query_vector, batch)
                matches_faces.extend(batch_matches)
                batch = []

        # Process remaining items
        if batch:
            batch_matches = await process_batch(query_vector, batch)
            matches_faces.extend(batch_matches)

        # Sort results
        matches_faces = sorted(matches_faces, key=lambda x: x['similarity'], reverse=True)

        # Clear cache if too many entries
        if len(calculate_similarity.cache_info().currsize) > CACHE_SIZE * 0.9:
            calculate_similarity.cache_clear()

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
        data={"matches": matches_faces}
    )
