import asyncio
import gc
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, List

import dlib
import numpy as np
import cv2
from fastapi import HTTPException
from PIL import Image, UnidentifiedImageError
from io import BytesIO

from insightface.app import FaceAnalysis

face_rec_model = dlib.face_recognition_model_v1("model/dlib_face_recognition_resnet_model_v1.dat")
shape_predictor = dlib.shape_predictor("model/shape_predictor_68_face_landmarks.dat")
detector = dlib.get_frontal_face_detector()

face_analyzer = None

executor = ThreadPoolExecutor(max_workers=3)

def initialize_insightface():
    global face_analyzer
    if face_analyzer is None:
        face_analyzer = FaceAnalysis(providers=['CPUExecutionProvider'])
        face_analyzer.prepare(ctx_id=0, det_size=(640, 640))
    return face_analyzer


async def detect_faces_with_insightface(img_bytes, is_main_face=True, max_faces=20):
    try:
        analyzer = initialize_insightface()

        img_bytes.seek(0)

        with Image.open(img_bytes) as pil_image:
            img_array = np.array(pil_image)
            if len(img_array.shape) == 2:  # แปลงภาพขาวดำเป็น RGB
                img_array = cv2.cvtColor(img_array, cv2.COLOR_GRAY2RGB)
            elif img_array.shape[2] == 4:  # แปลง RGBA เป็น RGB
                img_array = cv2.cvtColor(img_array, cv2.COLOR_RGBA2RGB)

        # ตรวจจับใบหน้า
        faces = analyzer.get(img_array)
        if not faces:
            return None

        # จัดเรียงใบหน้าตามขนาด (ใหญ่ไปเล็ก)
        faces = sorted(faces, key=lambda x: x.bbox[2] * x.bbox[3], reverse=True)

        face_embeddings = []
        if is_main_face:
            # เฉพาะใบหน้าที่ใหญ่ที่สุด
            embedding = faces[0].embedding
            print("Embedding size:", len(embedding))
            # ตรวจสอบขนาดเวกเตอร์
            if embedding is not None and len(embedding) == 512:
                face_embeddings.append(embedding)
            else:
                print(f"ขนาดเวกเตอร์ไม่ถูกต้อง: {len(embedding) if embedding is not None else 'None'}")
        else:
            # หลายใบหน้าตามขีดจำกัด
            for face in faces[:max_faces]:
                embedding = face.embedding
                # ตรวจสอบขนาดเวกเตอร์
                if embedding is not None and len(embedding) == 512:
                    face_embeddings.append(embedding)
                else:
                    print(f"ขนาดเวกเตอร์ไม่ถูกต้อง: {len(embedding) if embedding is not None else 'None'}")

        return face_embeddings if face_embeddings else None

    except Exception as e:
        print(f"เกิดข้อผิดพลาดในการตรวจจับใบหน้า: {str(e)}")
        return None
    finally:
        # ล้างหน่วยความจำ
        gc.collect()
