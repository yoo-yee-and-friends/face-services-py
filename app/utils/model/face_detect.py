import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, List

import dlib
import numpy as np
import cv2
from fastapi import HTTPException
from PIL import Image, UnidentifiedImageError
from io import BytesIO

face_rec_model = dlib.face_recognition_model_v1("model/dlib_face_recognition_resnet_model_v1.dat")
shape_predictor = dlib.shape_predictor("model/shape_predictor_68_face_landmarks.dat")
detector = dlib.get_frontal_face_detector()

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
    """Memory-safe face detection implementation"""
    try:
        # Reset file pointer
        image_bytes.seek(0)

        # Process image in chunks to reduce memory usage
        with Image.open(image_bytes) as pil_image:
            # Convert to numpy array with controlled memory
            img_array = np.asarray(pil_image, dtype=np.uint8)
            img = cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

            # Free memory
            del img_array

            # Detect faces
            faces = detector(gray, 1)  # Reduce upsampling to save memory
            if not faces:
                return None

            face_embeddings = []
            if is_main_face:
                # Process only largest face
                largest_face = max(faces, key=lambda face: face.width() * face.height())
                shape = shape_predictor(gray, largest_face)
                descriptor = np.array(face_rec_model.compute_face_descriptor(img, shape))
                face_embeddings.append(descriptor)

            else:
                # Process multiple faces efficiently
                faces_data = []
                for face in faces:
                    shape = shape_predictor(gray, face)
                    area = face.width() * face.height()
                    landmarks = np.array([[p.x, p.y] for p in shape.parts()])
                    score = area * np.sum(np.std(landmarks, axis=0))
                    faces_data.append((face, score, shape))

                # Sort and limit before computing expensive descriptors
                faces_data.sort(key=lambda x: x[1], reverse=True)
                for face, _, shape in faces_data[:max_faces]:
                    descriptor = np.array(face_rec_model.compute_face_descriptor(img, shape))
                    face_embeddings.append(descriptor)

            # Clean up
            del img
            del gray
            return face_embeddings

    except Exception as e:
        print(f"Error in face detection: {e}")
        return None

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
