import os
import shutil
from tempfile import NamedTemporaryFile

import dlib
import numpy as np
import cv2
from fastapi import HTTPException
from PIL import Image, UnidentifiedImageError
from io import BytesIO

from app.services.s3_service import check_and_download_models

check_and_download_models()

face_rec_model = dlib.face_recognition_model_v1("model/dlib_face_recognition_resnet_model_v1.dat")
shape_predictor = dlib.shape_predictor("model/shape_predictor_68_face_landmarks.dat")
detector = dlib.get_frontal_face_detector()

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

    faces = detector(gray)
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
    
async def process_image_faces(image):
    query_vector = await detect_faces_with_dlib(image, False)
    return query_vector

def compute_face_descriptor_with_dlib(rgb_image, face):
    shape = shape_predictor(rgb_image, face)
    face_descriptor = np.array(face_rec_model.compute_face_descriptor(rgb_image, shape))
    return face_descriptor

def compute_main_face_descriptor_with_dlib(image, faces):
    main_face = max(faces, key=lambda rect: rect.width() * rect.height())
    shape = shape_predictor(image, main_face)
    return np.array(face_rec_model.compute_face_descriptor(image, shape))
