import numpy as np
from typing import List
from app.utils.model.face_detect import process_image_main_face, process_image_faces
from app.db.queries.image_queries import insert_image, insert_face_vector, get_images_with_vectors
from app.services.dropbox_service import upload_to_dropbox
from sqlalchemy.orm import Session
import json
import traceback
from scipy.spatial.distance import cosine

async def save_image_and_vectors(files: List, db: Session):
    saved_files = []
    for file in files:
        try:

            vectors = await process_image_faces(file)

            preview_url, download_url = upload_to_dropbox(file, f"/{file.filename}")

            if not preview_url or not download_url:
                raise Exception("Error uploading file to Dropbox")

            image_id = insert_image(db, file.filename, preview_url, download_url)
            for vector in vectors:
                if isinstance(vector, np.ndarray):
                    vector = vector.tolist()
                insert_face_vector(db, image_id, json.dumps(vector))

            saved_files.append({"file_name": file.filename, "file_path": file})
        
        except Exception as e:
            print(f"Error processing file: {file.filename}")
            print(f"Error message: {str(e)}")
            traceback_lines = traceback.format_exc().splitlines()
            error_line = traceback_lines[-2]
            print(f"Error occurred at: {error_line}")
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
                    "file_path": record.image.preview_url,
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
            # แสดงบรรทัดของโค้ดที่เกิดปัญหา
        traceback_lines = traceback.format_exc().splitlines()
        error_line = traceback_lines[-2]  # บรรทัดที่เกิดปัญหา
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
