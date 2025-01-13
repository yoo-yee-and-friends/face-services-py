import base64
import json

from fastapi import APIRouter, File, UploadFile, HTTPException, Depends, WebSocket, WebSocketDisconnect,status
from starlette.datastructures import Headers

from app.db.models import User
from app.db.models.Album import Album
from app.services.image_services import save_image_and_vectors, find_similar_faces
from app.db.session import get_db
from sqlalchemy.orm import Session
import io
from app.api.v1.auth import router as auth_router, get_current_active_user, check_staff_user

router = APIRouter()
router.include_router(auth_router, prefix="/auth", tags=["auth"])

@router.post("/upload-images/")
async def upload_images(
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    try:
        response = await save_image_and_vectors(files, db)
        return response
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.websocket("/ws/upload-images/")
async def websocket_upload_images(
    websocket: WebSocket,
    db: Session = Depends(get_db),
    current_user: User = Depends(check_staff_user)
):
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            if message['fileName'] == "END":
                break

            album_name = message['albumName']
            album = db.query(Album).filter(Album.name == album_name, Album.creator_id == current_user.id).first()
            if not album:
                await websocket.send_text(f"Error: Album '{album_name}' not found")
                continue

            file_name = message['fileName']
            file_data = base64.b64decode(message['fileData'])
            file_bytes = io.BytesIO(file_data)

            headers = Headers({"Content-Type": "application/octet-stream"})
            upload_file = UploadFile(filename=file_name, file=file_bytes, headers=headers)
            upload_file.size = len(file_data)  # Manually set the size

            response = await save_image_and_vectors([upload_file], db)
            album.file_count += 1
            album.total_size += upload_file.size
            db.commit()
            await websocket.send_json(response)

    except WebSocketDisconnect:
        print("Client disconnected")
    except Exception as e:
        await websocket.send_text(f"Error: {str(e)}")

@router.post("/search-image")
@router.post("/search-image/")
async def search_image(file: UploadFile, db: Session = Depends(get_db)):
    try:
        print("Searching for similar faces")
        response = await find_similar_faces(file, db)
        return response
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/crate-album/")
async def create_album(name: str, db: Session = Depends(get_db), current_user: User = Depends(check_staff_user)):
    new_album = Album(name=name, creator_id=current_user.id)
    db.add(new_album)
    db.commit()
    db.refresh(new_album)
    return new_album

@router.put("/albums/{album_id}")
async def update_album(album_id: int, name: str, db: Session = Depends(get_db), current_user: User = Depends(check_staff_user)):
    album = db.query(Album).filter(Album.id == album_id, Album.creator_id == current_user.id).first()
    if not album:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Album not found")
    album.name = name
    db.commit()
    db.refresh(album)
    return album

@router.delete("/albums/{album_id}")
async def delete_album(album_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    album = db.query(Album).filter(Album.id == album_id, Album.creator_id == current_user.id).first()
    if not album:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Album not found")
    db.delete(album)
    db.commit()
    return {"detail": "Album deleted"}

@router.get("/albums/")
async def get_user_albums(db: Session = Depends(get_db), current_user: User = Depends(check_staff_user)):
    albums = db.query(Album).filter(Album.creator_id == current_user.id).all()
    return albums

