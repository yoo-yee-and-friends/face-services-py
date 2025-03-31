import asyncio
import time
from functools import lru_cache
from io import BytesIO
from typing import List, Dict

import numpy as np
from fastapi import UploadFile

from app.schemas.user import Response
from app.services.digital_oceans import generate_presigned_url
from app.utils.model.face_detect import process_image_main_face, _detect_faces_safe, detect_faces_with_insightface
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
    query_vector = np.array(query_vector_tuple, dtype=np.float32)
    stored_vector = np.array(stored_vector_tuple, dtype=np.float32)

    # ตรวจสอบความถูกต้องของเวกเตอร์
    if np.isnan(query_vector).any() or np.isnan(stored_vector).any():
        return 0.0  # ถ้ามีค่า NaN ถือว่าไม่เหมือนกัน

    # ป้องกัน zero-length vector
    if np.all(query_vector == 0) or np.all(stored_vector == 0):
        return 0.0

    return 1 - cosine(query_vector, stored_vector)

async def process_batch(query_vector: np.ndarray, batch: List[Dict], threshold: float = THRESHOLD) -> List[Dict]:
    matches = []
    query_vector = np.ravel(query_vector)

    for record in batch:
        # Handle the vector data based on its type
        if isinstance(record.vector, str):
            vector = np.array(json.loads(record.vector), dtype=np.float32)
        elif isinstance(record.vector, (list, np.ndarray)):
            vector = np.array(record.vector, dtype=np.float32)
        else:
            continue

        vector = np.ravel(vector)

        # Convert numpy arrays to tuples for caching
        similarity = calculate_similarity(tuple(query_vector), tuple(vector))

        if similarity >= threshold:
            matches.append({
                "id": record.id,
                "similarity": float(similarity),
                "file_name": record.photo.file_name,
                "uploaded_at": record.photo.uploaded_at,
                "preview_url": generate_presigned_url(
                    f"{record.photo.file_path}preview/{record.photo.file_name}"),
                "download_url": generate_presigned_url(f"{record.photo.file_path}{record.photo.file_name}")
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

async def find_similar_faces(event_id: int, file: UploadFile, db: Session):
    matches_faces = []
    try:
        print("Processing Start")
        print("Processing image:", file.filename)
        threshold = 0.45

        # อ่านไฟล์เพียงครั้งเดียว
        file_content = await file.read()
        file_bytes = BytesIO(file_content)

        # เรียกใช้ InsightFace แทน dlib
        query_vector = await detect_faces_with_insightface(file_bytes, is_main_face=True)

        if not query_vector or len(query_vector) == 0:
            return Response(
                message="ไม่พบใบหน้าในภาพที่อัปโหลด",
                status_code=400,
                status="Error",
                data={"matches": []}
            )

        # ใช้ใบหน้าแรกที่ตรวจพบ
        query_vector = query_vector[0]

        # ดึงเวกเตอร์จากฐานข้อมูล
        results = get_images_with_vectors(db, event_id)

        # ประมวลผลเป็นชุดๆ เพื่อประสิทธิภาพ
        for i in range(0, len(results), BATCH_SIZE):
            batch = results[i:i + BATCH_SIZE]
            batch_matches = await process_batch(query_vector, batch, threshold)
            matches_faces.extend(batch_matches)
            await asyncio.sleep(0)  # คืนการควบคุม

        matches_faces = sorted(matches_faces, key=lambda x: x['uploaded_at'])

    except Exception as e:
        print(f"เกิดข้อผิดพลาดในการประมวลผลไฟล์: {file.filename}")
        print(f"ข้อความผิดพลาด: {str(e)}")
        traceback_lines = traceback.format_exc().splitlines()
        error_line = traceback_lines[-2]
        print(f"ข้อผิดพลาดเกิดขึ้นที่: {error_line}")
        return Response(
            message=f"เกิดข้อผิดพลาดในการประมวลผลภาพ: {str(e)}",
            status_code=500,
            status="Error",
            data={"matches": []}
        )

    message = "พบภาพที่ตรงกัน" if matches_faces else "ไม่พบภาพที่ตรงกัน"
    if matches_faces:
        return Response(
            message=message,
            status_code=200,
            status="Success",
            data={"matches": matches_faces}
        )
    else:
        return Response(
            message=message,
            status_code=404,  # ใช้ 404 เพื่อแสดงว่าไม่พบข้อมูล
            status="NotFound",
            data={"matches": []}
        )

