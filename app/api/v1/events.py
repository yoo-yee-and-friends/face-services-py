import asyncio
import base64
import concurrent
import gc
import io
import json
import logging
import multiprocessing
import threading
import time
from concurrent.futures import ProcessPoolExecutor
from contextlib import closing
from datetime import datetime
from functools import partial
from http.client import responses

import numpy as np
import psutil
from PIL import Image
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, HTTPException, Query, UploadFile, File, Form, \
    BackgroundTasks
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from sqlalchemy import false
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.db.models import City
from app.db.models.Country import Country
from app.db.models.EventCreditType import EventCreditType
from app.db.models.EventFolder import EventFolder
from app.db.models.EventFolderPhoto import EventFolderPhoto
from app.db.models.EventPhoto import EventPhoto
from app.db.models.EventType import EventType
from app.db.models.PhotoFaceVector import PhotoFaceVector
from app.db.queries.image_queries import insert_face_vector
from app.db.session import get_db, SessionLocal
from app.schemas.event import Event as EventSchema, EventCreate, Credit
from app.schemas.user import Response
from app.security.auth import get_current_active_user, get_ws_current_active_user
from typing import List, Dict, Any, Optional

from app.services.digital_oceans import upload_file_to_spaces, generate_presigned_url, create_folder_in_spaces, \
    check_duplicate_name, upload_files_to_spaces, delete_file_from_spaces

from app.db.models.User import User
from app.db.models.Photo import Photo
from app.db.models.Event import Event
from app.db.models.EventCredit import EventCredit
from app.utils.event_utils import get_event_query, paginate_query, format_event_data
from app.utils.model.face_detect import detect_faces_with_dlib_in_event
from app.utils.validation import validate_date_format

router = APIRouter()
logger = logging.getLogger(__name__)

class UploadProgressLogger:
    def __init__(self, total_files: int, event_id: int):
        self.total_files = total_files
        self.processed_files = 0
        self.successful_files = 0
        self.failed_files = 0
        self.event_id = event_id
        self.start_time = time.time()

    def to_dict(self) -> dict:
        return {
            "total_files": self.total_files,
            "processed_files": self.processed_files,
            "successful_files": self.successful_files,
            "failed_files": self.failed_files,
            "remaining_files": self.total_files - self.processed_files,
            "progress_percentage": round((self.processed_files / self.total_files) * 100, 2),
            "elapsed_time": round(time.time() - self.start_time, 2)
        }


@router.get("/events", response_model=Response)
def get_events(
    page: int = 1,
    limit: int = 10,
    status: Optional[bool] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    if page < 1:
        return Response(
            message="Page number must be greater than 0",
            status="error",
            status_code=400
        )

    query = get_event_query(db, current_user, status, search)
    total_events = query.count()
    events = paginate_query(query, page, limit)
    total_pages = (total_events + limit - 1) // limit
    events_data = format_event_data(events)

    return Response(
        message="Events retrieved successfully",
        data={
            "total_events": total_events,
            "total_pages": total_pages,
            "current_page": page,
            "events_per_page": limit,
            "events": events_data
        },
        status="success",
        status_code=200
    )

@router.get("/prepare-event-create", response_model=Response)
def prepare_event_data(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    event_types = jsonable_encoder(db.query(EventType).all())
    countries = jsonable_encoder(db.query(Country).all())
    event_credit_types = jsonable_encoder(db.query(EventCreditType).all())

    return Response(
        message="Data retrieved successfully",
        data={
            "event_types": event_types,
            "countries": countries,
            "event_credit_types": event_credit_types
        },
        status="success",
        status_code=200
    )

@router.post("/create-event", response_model=Response)
def create_event(
    event_name: str = Form(...),
    event_type_id: int = Form(...),
    date: str = Form(...),
    location_name: str = Form(...),
    country_id: int = Form(...),
    city_id: int = Form(...),
    status: Optional[bool] = Form(False),
    cover_photo: UploadFile = File(...),
    credits: str = Form("[]"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    try:
        credit_list = json.loads(credits)
        credit_objects = [Credit(**credit) for credit in credit_list]

        is_validate_date_format = validate_date_format(date)
        if not is_validate_date_format:
            return Response(
                message="Invalid date format. Please use YYYY-MM-DD",
                status="error",
                status_code=400
            )

        event_date = datetime.strptime(date, "%Y-%m-%d")
        publish_at = datetime.utcnow() if status else None
        new_event = Event(
            event_name=event_name,
            event_type_id=event_type_id,
            date=event_date,
            location=location_name,
            country_id=country_id,
            city_id=city_id,
            status=status,
            user_id=current_user.id,
            publish_at=publish_at
        )
        db.add(new_event)
        db.commit()
        db.refresh(new_event)

        try:
            file_path = f"{current_user.id}/{new_event.id}/settings/{cover_photo.filename}"
            cover_photo.file.seek(0)
            file_content = cover_photo.file.read()
            cover_photo_path = upload_file_to_spaces(cover_photo, file_path)
            if not cover_photo_path:
                return Response(
                    message="Error uploading cover photo",
                    status="error",
                    status_code=500
                )

            cover_photo_size = len(file_content)
            new_photo = Photo(
                file_name=cover_photo.filename,
                size=cover_photo_size,
                file_path=f"{current_user.id}/{new_event.id}/settings/",
            )
            db.add(new_photo)
            db.commit()
            db.refresh(new_photo)

            new_event.cover_photo_id = new_photo.id
            db.commit()
        except Exception as e:
            db.delete(new_event)
            db.commit()
            logger.error(f"Error uploading cover photo: {e}")
            return Response(
                message="Error uploading cover photo: " + str(e),
                status="error",
                status_code=500
            )

        for credit in credit_objects:
            new_credit = EventCredit(
                event_id=new_event.id,
                event_credit_type_id=credit.credit_type_id,
                name=credit.name
            )
            db.add(new_credit)
        db.commit()

        return get_events(db=db, current_user=current_user)
    except Exception as e:
        logger.error(f"Error creating event: {e}")
        return Response(
            message="Error creating event: " + str(e),
            status="error",
            status_code=500
        )

@router.get("/event-details", response_model=Response)
def get_event_details(
    event_id: int,
    page: int = 1,
    limit: int = 10,
    search: Optional[str] = None,
    sort_by: Optional[str] = "name",
    sort_order: Optional[str] = "asc",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    if page < 1:
        return Response(
            message="Page number must be greater than 0",
            status="error",
            status_code=400
        )

    event = db.query(Event).filter(Event.id == event_id, Event.user_id == current_user.id).first()
    if not event:
        return Response(
            message="Event not found",
            status="error",
            status_code=404
        )

    # Query event folders
    folder_query = db.query(EventFolder).filter(EventFolder.event_id == event_id)
    if search:
        folder_query = folder_query.filter(EventFolder.name.ilike(f"%{search}%"))

    if sort_by == "name":
        if sort_order == "asc":
            folder_query = folder_query.order_by(EventFolder.name.asc())
        else:
            folder_query = folder_query.order_by(EventFolder.name.desc())
    elif sort_by == "date":
        if sort_order == "asc":
            folder_query = folder_query.order_by(EventFolder.updated_at.asc())
        else:
            folder_query = folder_query.order_by(EventFolder.updated_at.desc())

    total_folders = folder_query.count()
    skip = (page - 1) * limit
    event_folders = folder_query.offset(skip).limit(limit).all()
    total_folder_pages = (total_folders + limit - 1) // limit

    # Query photos related to event
    photo_query = db.query(Photo).join(EventPhoto).filter(EventPhoto.event_id == event_id)
    if search:
        photo_query = photo_query.filter(Photo.file_name.ilike(f"%{search}%"))

    if sort_by == "name":
        if sort_order == "asc":
            photo_query = photo_query.order_by(Photo.file_name.asc())
        else:
            photo_query = photo_query.order_by(Photo.file_name.desc())
    elif sort_by == "date":
        if sort_order == "asc":
            photo_query = photo_query.order_by(Photo.uploaded_at.asc())
        else:
            photo_query = photo_query.order_by(Photo.uploaded_at.desc())

    total_photos = photo_query.count()
    photos = photo_query.offset(skip).limit(limit).all()
    photos_data = [
        {
            "id": photo.id,
            "uploaded_at": photo.uploaded_at,
            "file_name": photo.file_name,
            "preview_url": generate_presigned_url(
                f"{photo.file_path}preview_{photo.file_name}"
            )
        }
        for photo in photos
    ]
    total_photo_pages = (total_photos + limit - 1) // limit

    return Response(
        message="Data retrieved successfully",
        data={
            "event": jsonable_encoder(event),
            "total_folders": total_folders,
            "total_folder_pages": total_folder_pages,
            "folders_per_page": limit,
            "folders": jsonable_encoder(event_folders),
            "total_photos": total_photos,
            "total_photo_pages": total_photo_pages,
            "photos_per_page": limit,
            "photos": jsonable_encoder(photos_data)
        },
        status="success",
        status_code=200
    )

@router.get("/folder-details", response_model=Dict[str, Any])
def get_folder_details(
    folder_id: int,
    page: int = 1,
    limit: int = 10,
    search: Optional[str] = None,
    sort_by: Optional[str] = "name",
    sort_order: Optional[str] = "asc",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    if page < 1:
        return Response(
            message="Page number must be greater than 0",
            status="error",
            status_code=400
        )

    folder = db.query(EventFolder).filter(EventFolder.id == folder_id).first()
    if not folder:
        return Response(
            message="Folder not found",
            status="error",
            status_code=404
        )

    photo_query = db.query(Photo).join(EventFolderPhoto).filter(EventFolderPhoto.event_folder_id == folder_id)
    if search:
        photo_query = photo_query.filter(Photo.file_name.ilike(f"%{search}%"))

    if sort_by == "name":
        if sort_order == "asc":
            photo_query = photo_query.order_by(Photo.file_name.asc())
        else:
            photo_query = photo_query.order_by(Photo.file_name.desc())
    elif sort_by == "date":
        if sort_order == "asc":
            photo_query = photo_query.order_by(Photo.uploaded_at.asc())
        else:
            photo_query = photo_query.order_by(Photo.uploaded_at.desc())

    total_photos = photo_query.count()
    skip = (page - 1) * limit
    photos = photo_query.offset(skip).limit(limit).all()
    photos_data = [
        {
            "id": photo.id,
            "uploaded_at": photo.uploaded_at,
            "file_name": photo.filen_ame.split('/')[-1],
            "preview_url": generate_presigned_url(
                f"{photo.file_path}/preview_{photo.file_name}")
        }
        for photo in photos
    ]
    total_photo_pages = (total_photos + limit - 1) // limit

    return Response(
        message="Data retrieved successfully",
        data={
            "folder": jsonable_encoder(folder),
            "total_photos": total_photos,
            "total_photo_pages": total_photo_pages,
            "photos_per_page": limit,
            "photos": jsonable_encoder(photos_data)
        },
        status="success",
        status_code=200
    )


@router.delete("/delete-event", response_model=Response)
def delete_event(
        event_id: int,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_active_user)
):
    event = db.query(Event).filter(Event.id == event_id, Event.user_id == current_user.id).first()
    if not event:
        return Response(
            message="Event not found",
            status="error",
            status_code=404
        )

    try:
        # Delete cover photo if it exists
        if event.cover_photo_id:
            cover_photo = db.query(Photo).filter(Photo.id == event.cover_photo_id).first()
            if cover_photo:
                # Store file paths before deletion
                cover_paths = [
                    f"{cover_photo.file_path}{cover_photo.file_name}",
                    f"{cover_photo.file_path}preview_{cover_photo.file_name}"
                ]
                for path in cover_paths:
                    delete_file_from_spaces(path)
                db.delete(cover_photo)
                db.commit()

        # Get all photos in event with their paths
        photos = db.query(Photo).join(EventPhoto).filter(EventPhoto.event_id == event_id).all()
        for photo in photos:
            # Store paths before deletion
            photo_paths = [
                f"{photo.file_path}{photo.file_name}",
                f"{photo.file_path}preview_{photo.file_name}"
            ]
            # Delete vectors first
            db.query(PhotoFaceVector).filter(PhotoFaceVector.photo_id == photo.id).delete()
            db.query(EventPhoto).filter(EventPhoto.photo_id == photo.id).delete()
            db.delete(photo)
            db.commit()

            # Delete files after database cleanup
            for path in photo_paths:
                delete_file_from_spaces(path)

        # Handle folders
        folders = db.query(EventFolder).filter(EventFolder.event_id == event_id).all()
        for folder in folders:
            # Get folder photos with their paths
            folder_photos = db.query(Photo).join(EventFolderPhoto).filter(
                EventFolderPhoto.event_folder_id == folder.id
            ).all()

            for photo in folder_photos:
                # Store paths before deletion
                photo_paths = [
                    f"{photo.file_path}{photo.file_name}",
                    f"{photo.file_path}preview_{photo.file_name}"
                ]
                # Delete database records first
                db.query(PhotoFaceVector).filter(PhotoFaceVector.photo_id == photo.id).delete()
                db.query(EventFolderPhoto).filter(
                    EventFolderPhoto.photo_id == photo.id,
                    EventFolderPhoto.event_folder_id == folder.id
                ).delete()
                db.delete(photo)
                db.commit()

                # Delete files after database cleanup
                for path in photo_paths:
                    delete_file_from_spaces(path)

            db.delete(folder)
            db.commit()

        # Finally delete the event
        db.delete(event)
        db.commit()

        return Response(
            message="Event deleted successfully",
            status="success",
            status_code=200
        )

    except Exception as e:
        db.rollback()
        logger.error(f"Error deleting event: {str(e)}")
        return Response(
            message=f"Error deleting event: {str(e)}",
            status="error",
            status_code=500
        )

@router.websocket("/ws/upload-photos")
async def websocket_upload_images(
    websocket: WebSocket,
    event_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_ws_current_active_user)
):
    event = db.query(Event).filter(Event.id == event_id, Event.user_id == current_user.id).first()
    if not event:
        await websocket.close(code=1008)  # Close with policy violation code
        return
    await websocket.accept()
    ping_interval = 60  # 5 minutes
    try:
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=ping_interval)
                message = json.loads(data)
                message_type = message.get('type')

                if message_type == "upload_file":
                    if 'file_name' in message and 'file_data' in message:
                        if message['file_name'] == "END":
                            print("Received END signal, stopping upload.")
                            break
                    resource = await handle_file_upload(message, event_id, current_user, db)
                    if 'error' in resource:
                        await websocket.send_json(resource)
                        await websocket.close(code=1011)
                        break
                    await websocket.send_json(resource)
                elif message_type == "upload_file_in_folder":
                    if 'file_name' in message and 'file_data' in message and 'folder_id' in message:
                        if message['file_name'] == "END":
                            break
                    response = await handle_file_upload(message, event_id, current_user, db, message['folder_id'])
                    if 'error' in response:
                        await websocket.send_json(response)
                        await websocket.close(code=1011)
                        break
                    await websocket.send_json(response)
                elif message_type == "create_folder":
                    await create_folder(websocket, event_id, current_user, db, message['folder_name'])
                elif message_type == "delete_file":
                    if 'file_id' in message:
                        await delete_file(websocket, event_id, message['file_id'], db)
                elif message_type == "delete_file_in_folder":
                    if 'file_id' in message and 'folder_id' in message:
                        await delete_file(websocket, event_id, message['file_id'], db, message['folder_id'])
                elif message_type == "delete_folder":
                    if 'folder_id' in message:
                        await delete_folder(websocket, event_id, message['folder_id'], db)

            except asyncio.TimeoutError:
                await websocket.send_json({
                    "message": "No activity for 5 minutes, closing connection.",
                    "status": "error",
                    "status_code": 408
                })
                await websocket.close(code=1000)
                break

    except WebSocketDisconnect as e:
        print("Client disconnected:" + str(e))
    except Exception as e:
        logger.error(f"Error during file upload: {str(e)}")
        await websocket.send_json({
            "message": f"Error during file upload: {str(e)}",
            "status": "error",
            "status_code": 500
        })
        await websocket.close(code=1011)

async def handle_file_upload(
    message: dict,
    event_id: int,
    current_user: User,
    db: Session,
    folder_id: Optional[int] = None
):
    file_path = f"{current_user.id}/{event_id}"
    if folder_id:
        event_folder = db.query(EventFolder).filter(EventFolder.event_id == event_id, EventFolder.id == folder_id).first()
        if not event_folder:
            return {"error": "Folder not found", "status_code": 404}
        file_path += f"/{event_folder.name}"

    file_name = check_duplicate_name(message['file_name'], file_path, False)
    full_path = f"{file_path}/{file_name}"
    file_data = base64.b64decode(message['file_data'])
    file_bytes = io.BytesIO(file_data)

    upload_files_to_spaces(file_bytes, full_path)

    preview_data = base64.b64decode(message['file_data'])
    preview_bytes = io.BytesIO(preview_data)

    preview_bytes.seek(0)
    with Image.open(preview_bytes) as image:
        image = image.convert("RGB")
        max_size = (image.width // 2, image.height // 2)
        image.thumbnail(max_size, Image.LANCZOS)

        preview_bytes = io.BytesIO()
        image.save(preview_bytes, format="WEBP", quality=50, optimize=True)
        preview_path = f"{file_path}/preview_{file_name}"
        preview_bytes.seek(0)

        upload_files_to_spaces(preview_bytes, preview_path)

    face_detected_bytes = io.BytesIO(file_data)
    vectors = await detect_faces_with_dlib_in_event(face_detected_bytes, False)

    new_photo = Photo(
        file_name=file_name,
        file_path=f"{file_path}/",
        size=len(file_data),
        is_detected_face=(vectors is not None),
    )

    try:
        db.add(new_photo)
        db.commit()
        db.refresh(new_photo)

        if vectors is not None:
            for vector in vectors:
                if isinstance(vector, np.ndarray):
                    vector = vector.tolist()
                insert_face_vector(db, new_photo.id, json.dumps(vector))
            print(f"Face vectors for photo {file_name} inserted into database.")

        if folder_id:
            event_folder_photo = EventFolderPhoto(
                event_folder_id=folder_id,
                photo_id=new_photo.id
            )
            db.add(event_folder_photo)
            db.commit()
            db.refresh(event_folder_photo)

            event_folder.total_photo_count += 1
            event_folder.total_photo_size += len(file_data)
            event_folder.updated_at = datetime.utcnow()
            db.commit()
        else:
            event_photo = EventPhoto(
                event_id=event_id,
                photo_id=new_photo.id
            )
            db.add(event_photo)
            db.commit()
            db.refresh(event_photo)

        event = db.query(Event).filter(Event.id == event_id).first()
        event.total_image_count += 1
        event.total_image_size += len(file_data)
        event.updated_at = datetime.utcnow()
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Error saving photo {file_name} to database: {str(e)}")
        return {"error": f"Error saving photo to database: {str(e)}", "status_code": 500}

    return {
        "message": f"File {file_name} uploaded successfully",
        "status": "success",
        "status_code": 200,
        "data": {
            "photo_id": new_photo.id,
            "uploaded_at": new_photo.uploaded_at.isoformat(),
            "file_name": new_photo.file_name,
            "preview_url": generate_presigned_url(f"{new_photo.file_path}/preview_{new_photo.file_name}")
        }
    }

async def create_folder(websocket: WebSocket, event_id: int, current_user: User, db: Session, folder_name: str):
    folder_path = f"{current_user.id}/{event_id}"
    folder_name = check_duplicate_name(f"{folder_name}/", folder_path, True)
    full_path = f"{current_user.id}/{event_id}/{folder_name}"

    create_folder_in_spaces(full_path)
    event_folder = EventFolder(
        event_id=event_id,
        name=folder_name
    )
    try:
        db.add(event_folder)
        db.commit()
        db.refresh(event_folder)
    except Exception as e:
        db.rollback()
        await websocket.send_json({
            "message": f"Error creating folder: {str(e)}",
            "status": "error",
            "status_code": 500
        })
        await websocket.close(code=1011)
        return

    await websocket.send_json({
        "message": f"Folder {folder_name} created successfully",
        "status": "success",
        "status_code": 200,
        "data": {
            "folder_id": event_folder.id,
            "folder_name": event_folder.name
        }
    })

async def delete_file(websocket: WebSocket, event_id: int, file_id: int, db: Session, folder_id: Optional[int] = None):
    query = db.query(Photo).join(EventPhoto)
    if folder_id:
        query = query.join(EventFolderPhoto).filter(EventFolderPhoto.event_folder_id == folder_id)
    photo = query.filter(Photo.id == file_id, EventPhoto.event_id == event_id).first()

    if not photo:
        return await websocket.send_json({
            "message": "File not found",
            "status": "error",
            "status_code": 404,
            "data": {"file_id": file_id, "folder_id": folder_id}
        })

    delete_file_from_spaces(f"{photo.file_path}{photo.file_name}")
    delete_file_from_spaces(f"{photo.file_path}preview_{photo.file_name}")

    # Update event file size and count
    event = db.query(Event).filter(Event.id == event_id).first()
    event.total_image_count -= 1
    event.total_image_size -= photo.size
    db.commit()

    if folder_id:
        # Update event folder file size and count
        event_folder = db.query(EventFolder).filter(EventFolder.id == folder_id).first()
        if event_folder:
            event_folder.total_photo_count -= 1
            event_folder.total_photo_size -= photo.size
            db.commit()

    # Delete photo vectors
    db.query(PhotoFaceVector).filter(PhotoFaceVector.photo_id == photo.id).delete()
    db.commit()

    if folder_id:
        # Delete from EventFolderPhoto
        db.query(EventFolderPhoto).filter(EventFolderPhoto.photo_id == photo.id,
                                          EventFolderPhoto.event_folder_id == folder_id).delete()
    else:
        # Delete from EventPhoto
        db.query(EventPhoto).filter(EventPhoto.photo_id == photo.id).delete()
    db.commit()

    # Delete photo from database
    db.delete(photo)
    db.commit()

    await websocket.send_json({
        "message": f"File {photo.file_name} deleted successfully",
        "status": "success",
        "status_code": 200,
        "data": {"file_id": photo.id}
    })

async def delete_folder(websocket: WebSocket, event_id: int, folder_id: int, db: Session):
    event_folder = db.query(EventFolder).filter(
        EventFolder.id == folder_id,
        EventFolder.event_id == event_id
    ).first()
    if not event_folder:
        return await websocket.send_json({
            "message": "Folder not found",
            "status": "error",
            "status_code": 404,
            "data": {"folder_id": folder_id}
        })

    # Delete all photos in folder
    photos = db.query(Photo).join(EventFolderPhoto).filter(
        EventFolderPhoto.event_folder_id == folder_id
    ).all()
    for photo in photos:
        delete_file_from_spaces(f"{photo.file_path}{photo.file_name}")
        delete_file_from_spaces(f"{photo.file_path}preview_{photo.file_name}")

        # Update event file size and count
        event = db.query(Event).filter(Event.id == event_id).first()
        event.total_image_count -= 1
        event.total_image_size -= photo.size
        db.commit()

        # Delete photo vectors
        db.query(PhotoFaceVector).filter(PhotoFaceVector.photo_id == photo.id).delete()
        db.commit()

        # Delete from EventFolderPhoto
        db.query(EventFolderPhoto).filter(
            EventFolderPhoto.photo_id == photo.id,
            EventFolderPhoto.event_folder_id == folder_id
        ).delete()
        db.commit()

        # Delete photo from database
        db.delete(photo)
        db.commit()

    # Delete folder
    db.delete(event_folder)
    db.commit()

    await websocket.send_json({
        "message": f"Folder {event_folder.name} deleted successfully",
        "status": "success",
        "status_code": 200,
        "data": {"folder_id": event_folder.id}
    })

active_connections: dict = {}

def insert_vector_to_db(photo_id: int, vector: np.ndarray) -> bool:
    """Insert single vector to database with its own session"""
    with closing(SessionLocal()) as db:
        try:
            vector_json = json.dumps(vector.tolist() if isinstance(vector, np.ndarray) else vector)
            insert_face_vector(db, photo_id, vector_json)
            db.commit()
            return True
        except Exception as e:
            db.rollback()
            logger.error(f"Error inserting vector for photo {photo_id}: {e}")
            return False

@router.websocket("/ws/upload")
async def websocket_endpoint(websocket: WebSocket, user_id: str):
    await websocket.accept()
    active_connections[user_id] = websocket
    try:
        while True:
            # Listen for incoming messages from the client (optional, can be used for control)
            data = await websocket.receive_text()
            print(f"Received message from event id {user_id}: {data}")
    except WebSocketDisconnect:
        del active_connections[user_id]
        print(f"Client {user_id} disconnected.")

@router.post("/upload-files", response_model=None)
async def handle_bulk_file_upload(
        event_id: int,
        files: List[UploadFile] = File(...),
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_active_user),
        folder_id: Optional[int] = None,
        batch_size: int = 5
):
    progress_logger = UploadProgressLogger(len(files), event_id)
    connection_id = f"{event_id}_{current_user.id}"
    websocket = active_connections.get(connection_id)

    event = db.query(Event).filter(Event.id == event_id, Event.user_id == current_user.id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    response_data = []

    for i in range(0, len(files), batch_size):
        batch = files[i:i + batch_size]
        tasks = []

        for file in batch:
            tasks.append(
                process_single_file(
                    file, event_id, current_user, db, folder_id,
                    progress_logger, websocket, event
                )
            )

        # รอให้ทุก task ใน batch เสร็จพร้อมกัน
        batch_results = await asyncio.gather(*tasks)
        response_data.extend([r for r in batch_results if r])

    return {
        "message": "Files processed successfully",
        "status": "success",
        "status_code": 200,
        "data": response_data,
    }


async def process_single_file(
        file: UploadFile,
        event_id: int,
        current_user: User,
        db: Session,
        folder_id: Optional[int],
        progress_logger: UploadProgressLogger,
        websocket: WebSocket,
        event: Event
) -> Optional[dict]:
    try:
        # Create file path
        file_path = f"{current_user.id}/{event_id}"
        if folder_id:
            event_folder = db.query(EventFolder).filter(
                EventFolder.event_id == event_id,
                EventFolder.id == folder_id
            ).first()
            if not event_folder:
                raise HTTPException(status_code=404, detail="Folder not found")
            file_path += f"/{event_folder.name}"

        file_name = check_duplicate_name(file.filename, file_path, False)
        full_path = f"{file_path}/{file_name}"

        # Read file content once
        file_content = await file.read()
        file_size = len(file_content)

        # Upload original file
        with io.BytesIO(file_content) as file_bytes:
            upload_files_to_spaces(file_bytes, full_path)

        # Create and upload preview
        with io.BytesIO(file_content) as preview_bytes:
            with Image.open(preview_bytes) as image:
                image = image.convert("RGB")
                max_size = (image.width // 2, image.height // 2)
                image.thumbnail(max_size, Image.LANCZOS)

                with io.BytesIO() as output:
                    image.save(output, format="WEBP", quality=50, optimize=True)
                    output.seek(0)
                    preview_path = f"{file_path}/preview_{file_name}"
                    upload_files_to_spaces(output, preview_path)

        # Process face detection
        with io.BytesIO(file_content) as face_bytes:
            vectors = await detect_faces_with_dlib_in_event(face_bytes, False)

        # Save to database
        new_photo = Photo(
            file_name=file_name,
            file_path=f"{file_path}/",
            size=file_size,
            is_detected_face=(vectors is not None),
        )
        db.add(new_photo)
        db.commit()
        db.refresh(new_photo)

        # Insert vectors if detected
        if vectors is not None:
            for vector in vectors:
                if isinstance(vector, np.ndarray):
                    # Ensure vector is float32 and has correct dimensions
                    vector = vector.astype(np.float32)
                    if vector.size != 128:
                        logger.error(f"Invalid vector dimension: {vector.size}")
                        continue
                vector_json = json.dumps(vector.tolist() if isinstance(vector, np.ndarray) else vector)
                insert_face_vector(db, new_photo.id, vector_json)
            db.commit()

        # Update relations
        if folder_id:
            event_folder_photo = EventFolderPhoto(
                event_folder_id=folder_id,
                photo_id=new_photo.id
            )
            db.add(event_folder_photo)
            event_folder.total_photo_count += 1
            event_folder.total_photo_size += file_size
            event_folder.updated_at = datetime.utcnow()
        else:
            event_photo = EventPhoto(
                event_id=event_id,
                photo_id=new_photo.id
            )
            db.add(event_photo)

        event.total_image_count += 1
        event.total_image_size += file_size
        event.updated_at = datetime.utcnow()
        db.commit()

        progress_logger.processed_files += 1
        progress_logger.successful_files += 1

        if websocket:
            await send_upload_progress(
                websocket,
                f"Processed {file.filename}",
                progress_logger,
                {"file_name": file.filename}
            )

        return {
            "photo_id": new_photo.id,
            "uploaded_at": new_photo.uploaded_at.isoformat(),
            "file_name": new_photo.file_name,
            "preview_url": generate_presigned_url(f"{new_photo.file_path}/preview_{new_photo.file_name}")
        }

    except Exception as e:
        progress_logger.failed_files += 1
        if websocket:
            await send_upload_progress(
                websocket,
                f"Failed to process {file.filename}",
                progress_logger,
                {"error": str(e)},
                "error"
            )
        logger.error(f"Error processing file {file.filename}: {str(e)}")
        return None
    finally:
        await file.close()
        gc.collect()

@router.websocket("/ws/upload-progress/{event_id}")
async def upload_progress_websocket(
        websocket: WebSocket,
        event_id: int,
        current_user: User = Depends(get_ws_current_active_user)
):
    try:
        await websocket.accept()
        connection_id = f"{event_id}_{current_user.id}"
        active_connections[connection_id] = websocket

        try:
            # ส่งข้อความยืนยันการเชื่อมต่อ
            await websocket.send_json({
                "type": "connected",
                "message": "Upload progress connection established",
                "event_id": event_id
            })

            # รอรับข้อความจาก client
            while True:
                data = await websocket.receive_json()
                if data.get("type") == "upload_complete":
                    # ส่งสถานะเสร็จสิ้นกลับไป
                    await websocket.send_json({
                        "type": "upload_status",
                        "status": "completed",
                        "message": "All files uploaded successfully"
                    })
                    break

        except WebSocketDisconnect:
            print(f"Client disconnected: {connection_id}")
        finally:
            if connection_id in active_connections:
                del active_connections[connection_id]

    except Exception as e:
        logging.error(f"WebSocket error: {str(e)}")
        await websocket.close(code=1011)

async def send_upload_progress(
    websocket: WebSocket,
    message: str,
    progress: UploadProgressLogger,
    data: dict = None,
    level: str = "info"
):
    """Send formatted progress update through WebSocket"""
    try:
        log_data = {
            "type": "upload_progress",
            "level": level,
            "message": message,
            "timestamp": datetime.utcnow().isoformat(),
            "progress": progress.to_dict(),
            "data": data
        }
        await websocket.send_json(log_data)
        logger.info(f"Progress sent: {message}")
    except Exception as e:
        logger.error(f"Error sending progress update: {e}")





