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

async def detect_faces_with_dlib_in_event(image_bytes, is_main_face=True, max_faces=20):
    """Detect faces using thread pool with improved memory management"""
    print("Detecting faces with dlib in event image")
    try:
        # Create new event loop for thread
        loop = asyncio.get_running_loop()

        # Run in thread pool with memory management
        result = await loop.run_in_executor(
            executor,
            _detect_faces_safe,
            image_bytes,
            is_main_face,
            max_faces
        )
        return result

    except Exception as e:
        print(f"Face detection error: {e}")
        return None

def _detect_faces_safe(image_bytes, is_main_face: bool, max_faces: int) -> Optional[List[np.ndarray]]:
    try:
        image_bytes.seek(0)

        with Image.open(image_bytes) as pil_image:
            # Convert to RGB for consistent processing
            pil_image = pil_image.convert('RGB')
            width, height = pil_image.size

            # Optimal size boundaries for face detection
            min_dimension = 300  # Minimum size for reliable detection
            optimal_dimension = 800  # Best balance of accuracy/performance
            max_dimension = 1024  # Maximum size limit

            # Calculate resize ratio
            if width < min_dimension or height < min_dimension:
                # Scale up small images
                scale = min_dimension / min(width, height)
            elif width > max_dimension or height > max_dimension:
                # Scale down large images
                scale = max_dimension / max(width, height)
            else:
                # Scale to optimal if within bounds
                scale = optimal_dimension / max(width, height)

            # Resize if needed
            if abs(scale - 1.0) > 0.1:  # Only resize if change is >10%
                new_size = (int(width * scale), int(height * scale))
                pil_image = pil_image.resize(new_size, Image.Resampling.LANCZOS)

            # Convert to numpy array efficiently
            img_array = np.asarray(pil_image, dtype=np.uint8)
            img = cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

            # Free up memory
            del img_array
            del pil_image

            # Face detection with optimal parameters
            faces = detector(gray, 1)  # upsample_num_times=1 for speed/accuracy balance
            if not faces:
                return None

            face_embeddings = []
            if is_main_face:
                # Get largest face by area
                largest_face = max(faces, key=lambda face: face.width() * face.height())
                shape = shape_predictor(gray, largest_face)
                face_area = largest_face.width() * largest_face.height()

                # Only process faces above minimum size
                min_face_area = (min_dimension * 0.2) ** 2  # 20% of min dimension
                if face_area >= min_face_area:
                    descriptor = np.array(face_rec_model.compute_face_descriptor(img, shape))
                    face_embeddings.append(descriptor)
            else:
                # Process multiple faces with quality scoring
                faces_data = []
                for face in faces:
                    face_area = face.width() * face.height()
                    if face_area >= (min_dimension * 0.2) ** 2:
                        shape = shape_predictor(gray, face)
                        # Quality score based on face size and landmark distribution
                        landmarks = np.array([[p.x, p.y] for p in shape.parts()])
                        score = face_area * np.sum(np.std(landmarks, axis=0))
                        faces_data.append((face, score, shape))

                # Process best faces up to max_faces limit
                faces_data.sort(key=lambda x: x[1], reverse=True)
                for face, _, shape in faces_data[:max_faces]:
                    descriptor = np.array(face_rec_model.compute_face_descriptor(img, shape))
                    face_embeddings.append(descriptor)

            return face_embeddings if face_embeddings else None

    except Exception as e:
        print(f"Error in face detection: {e}")
        return None
    finally:
        # Ensure cleanup
        gc.collect()

async def detect_faces_with_dlib(img, is_main_face=True):
    print("Detecting faces with dlib")
    face_embeddings = []
    print("Processing image:", img.filename)
    try:
        image_bytes = await img.read()
        pil_image = Image.open(BytesIO(image_bytes))
        img = cv2.cvtColor(np.array(pil_image), cv2.COLOR_RGB2BGR)
    except UnidentifiedImageError as e:
        raise HTTPException(status_code=400, detail=f"Invalid image file format {e}")

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    print("Gray image shape:", gray.shape)

    faces = detector(gray, 2)
    if not faces:
        print("No faces detected")
        return None
    print("Largest face: Before")
    largest_face = max(faces, key=lambda face: face.width() * face.height())
    print("Largest face:", largest_face)
    if is_main_face:
        shape = shape_predictor(gray, largest_face)
        main_face_embedding = face_rec_model.compute_face_descriptor(img, shape)
        face_embeddings.append(np.array(main_face_embedding))
    else:
        for face in faces:
            shape = shape_predictor(gray, face)
            face_descriptor = face_rec_model.compute_face_descriptor(img, shape)
            face_embeddings.append(np.array(face_descriptor))
    return face_embeddings

async def process_image_main_face(image):
    print("Processing image: process_image_main_face")

    query_vector = await detect_faces_with_dlib(image, True)
    return query_vector

def initialize_insightface():
    global face_analyzer
    if face_analyzer is None:
        face_analyzer = FaceAnalysis(providers=['CPUExecutionProvider'])
        face_analyzer.prepare(ctx_id=0, det_size=(640, 640))
    return face_analyzer


async def detect_faces_with_insightface(img_bytes, is_main_face=True, max_faces=20):
    """
    ตรวจจับใบหน้าด้วย InsightFace และส่งคืน embedding vectors พร้อมปรับขนาดภาพเพื่อประสิทธิภาพ

    Args:
        img_bytes: BytesIO object ของรูปภาพ
        is_main_face: ถ้า True จะส่งคืนเฉพาะใบหน้าที่ใหญ่สุด
        max_faces: จำนวนใบหน้าสูงสุดที่จะตรวจจับ

    Returns:
        List of face embedding vectors หรือ None ถ้าไม่พบใบหน้า
    """
    try:
        analyzer = initialize_insightface()

        # Reset pointer position
        img_bytes.seek(0)

        # แปลงข้อมูล bytes เป็นรูปภาพ
        with Image.open(img_bytes) as pil_image:
            # แปลงเป็น RGB เพื่อความสม่ำเสมอ
            pil_image = pil_image.convert('RGB')
            width, height = pil_image.size

            # ขนาดที่เหมาะสมสำหรับการตรวจจับใบหน้า
            min_dimension = 300
            optimal_dimension = 800
            max_dimension = 1024

            # คำนวณอัตราส่วนการปรับขนาด
            if width < min_dimension or height < min_dimension:
                # เพิ่มขนาดภาพเล็กเกินไป
                scale = min_dimension / min(width, height)
            elif width > max_dimension or height > max_dimension:
                # ลดขนาดภาพใหญ่เกินไป
                scale = max_dimension / max(width, height)
            else:
                # ปรับเป็นขนาดที่เหมาะสม
                scale = optimal_dimension / max(width, height)

            # ปรับขนาดหากจำเป็น (เมื่อเปลี่ยนแปลงมากกว่า 10%)
            if abs(scale - 1.0) > 0.1:
                new_size = (int(width * scale), int(height * scale))
                pil_image = pil_image.resize(new_size, Image.Resampling.LANCZOS)

            # แปลงเป็น numpy array
            img_array = np.asarray(pil_image)

        # ตรวจจับใบหน้า
        faces = analyzer.get(img_array)
        if not faces:
            return None

        # จัดเรียงใบหน้าตามขนาด (ใหญ่ไปเล็ก)
        faces = sorted(faces, key=lambda x: x.bbox[2] * x.bbox[3], reverse=True)

        face_embeddings = []
        if is_main_face:
            # เฉพาะใบหน้าที่ใหญ่ที่สุด
            face_embeddings.append(faces[0].embedding)
        else:
            # หลายใบหน้าตามขีดจำกัด
            for face in faces[:max_faces]:
                face_embeddings.append(face.embedding)

        return face_embeddings
    except Exception as e:
        print(f"Error in InsightFace detection: {e}")
        return None
