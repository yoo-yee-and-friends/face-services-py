from fastapi import APIRouter, File, UploadFile, HTTPException, Depends
from app.services.image_services import save_image_and_vectors, find_similar_faces
from app.db.session import get_db
from sqlalchemy.orm import Session

router = APIRouter()

@router.post("/upload-images/")
async def upload_images(files: list[UploadFile] = File(...), db: Session = Depends(get_db)):
    try:
        response = await save_image_and_vectors(files, db)
        return response
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/search-image")
@router.post("/search-image/")
async def search_image(file: UploadFile, db: Session = Depends(get_db)):
    try:
        print("Searching for similar faces")
        response = await find_similar_faces(file, db)
        return response
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
