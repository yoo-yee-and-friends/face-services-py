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

import boto3
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
    check_duplicate_name, upload_files_to_spaces, delete_file_from_spaces, generate_presigned_upload_url

from app.db.models.User import User
from app.db.models.Photo import Photo
from app.db.models.Event import Event
from app.db.models.EventCredit import EventCredit
from app.utils.event_utils import get_event_query, paginate_query, format_event_data
from app.utils.model.face_detect import detect_faces_with_dlib_in_event, detect_faces_with_insightface
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
                f"{photo.file_path}preview/{photo.file_name}"
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
                f"{photo.file_path}/preview/{photo.file_name}")
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
        page: int = 1,
        limit: int = 10,
        status: str = "",  # Changed to str to handle empty string case
        search: Optional[str] = None,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_active_user)
):
    status_filter = None
    if status:
        if status.lower() == "true":
            status_filter = True
        elif status.lower() == "false":
            status_filter = False

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
                    f"{cover_photo.file_path}preview/{cover_photo.file_name}"
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
                f"{photo.file_path}preview/{photo.file_name}"
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
                    f"{photo.file_path}preview/{photo.file_name}"
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

        # Get filtered events after deletion
        if page < 1:
            return Response(
                message="Page number must be greater than 0",
                status="error",
                status_code=400
            )

        query = get_event_query(db, current_user, status_filter, search)
        total_events = query.count()
        events = paginate_query(query, page, limit)
        total_pages = (total_events + limit - 1) // limit
        events_data = format_event_data(events)

        return Response(
            message="Event deleted successfully",
            status="success",
            status_code=200,
            data={
                "total_events": total_events,
                "total_pages": total_pages,
                "current_page": page,
                "events_per_page": limit,
                "events": events_data,
                "current_search": search or "",
                "current_status": status  # Use the original string value
            }
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
        preview_path = f"{file_path}/preview/{file_name}"
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
            "preview_url": generate_presigned_url(f"{new_photo.file_path}/preview/{new_photo.file_name}")
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
    delete_file_from_spaces(f"{photo.file_path}preview/{photo.file_name}")

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
        delete_file_from_spaces(f"{photo.file_path}preview/{photo.file_name}")

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
        batch_size: int = 5,
        background_tasks: BackgroundTasks = None
):
    event = db.query(Event).filter(Event.id == event_id, Event.user_id == current_user.id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    connection_id = f"{event_id}_{current_user.id}"
    websocket = active_connections.get(connection_id)
    progress_logger = UploadProgressLogger(len(files), event_id)

    if websocket:
        try:
            await send_upload_progress(
                websocket,
                f"Started processing {len(files)} files for face detection",
                progress_logger,
                {"total_files": len(files)}
            )
        except Exception as e:
            logger.error(f"Error sending initial WebSocket message: {str(e)}")

    background_tasks.add_task(
        process_files_in_background,
        files=files,
        event_id=event_id,
        current_user=current_user,
        db_session=db,
        folder_id=folder_id,
        batch_size=batch_size,
        progress_logger=progress_logger,
        connection_id=connection_id
    )

    return {
        "message": "File processing started in background...",
        "status": "success",
        "status_code": 202,
        "data": {
            "total_files": len(files),
            "event_id": event_id
        }
    }


@router.post("/batch-upload-urls", response_model=Response)
async def create_upload_urls(
        request: dict,
        current_user: User = Depends(get_current_active_user),
        db: Session = Depends(get_db)
):
    event_id = request.get("eventId")
    images = request.get("images", [])

    if not event_id:
        raise HTTPException(status_code=400, detail="Missing event ID")

    if not images:
        raise HTTPException(status_code=400, detail="No images provided")

    # Verify event exists and belongs to user
    event = db.query(Event).filter(
        Event.id == event_id,
        Event.user_id == current_user.id
    ).first()

    if not event:
        raise HTTPException(
            status_code=404,
            detail="Event not found or doesn't belong to the current user"
        )

    # Generate URLs for each image with duplicate detection
    result = []
    base_path = f"{current_user.id}/{event_id}"

    # Get existing files to check for duplicates
    s3_client = boto3.client('s3',
                             aws_access_key_id=settings.SPACES_ACCESS_KEY_ID,
                             aws_secret_access_key=settings.SPACES_SECRET_ACCESS_KEY,
                             endpoint_url=settings.SPACES_ENDPOINT)

    try:
        existing_files = s3_client.list_objects_v2(Bucket='snapgoated', Prefix=base_path)
        existing_names = [obj['Key'].split('/')[-1] for obj in existing_files.get('Contents', [])]
    except Exception as e:
        logger.error(f"Error checking existing files: {str(e)}")
        existing_names = []

    # Track names we've already processed in this batch
    processed_names = set(existing_names)

    for image in images:
        file_name = image.get("name", "")
        content_type = image.get("content_type", "image/jpeg")

        if not file_name:
            continue

        # แก้ไขชื่อไฟล์เพื่อป้องกันปัญหา
        clean_file_name = sanitize_filename(file_name)

        # ตรวจสอบซ้ำ
        is_duplicate = clean_file_name in processed_names
        new_file_name = clean_file_name

        if is_duplicate:
            # สร้างชื่อใหม่ถ้าซ้ำ
            name, ext = clean_file_name.rsplit('.', 1) if '.' in clean_file_name else (clean_file_name, '')
            counter = 1

            while f"{name}_{counter}.{ext}" in processed_names:
                counter += 1

            new_file_name = f"{name}_{counter}.{ext}" if ext else f"{name}_{counter}"

            # เพิ่มชื่อใหม่ในรายการที่ประมวลผลแล้ว
            processed_names.add(new_file_name)
        else:
            # เพิ่มชื่อเดิมในรายการที่ประมวลผลแล้ว
            processed_names.add(clean_file_name)

        # พาธไฟล์ต้นฉบับ
        file_path = f"{base_path}/{new_file_name}"

        # พาธไฟล์พรีวิว
        preview_file_path = f"{base_path}/preview/{new_file_name}"

        try:
            upload_url = generate_presigned_upload_url(
                file_path,
                3600,
                content_type
            )

            preview_url = generate_presigned_upload_url(
                preview_file_path,
                3600,
                content_type
            )

            result.append({
                "name": file_name,
                "url": upload_url,
                "url_preview": preview_url,
                "isDuplicate": is_duplicate,
                "newFileName": new_file_name
            })

        except Exception as e:
            logger.error(f"Error generating upload URL for {file_name}: {str(e)}")
            return Response(
                message=f"Error generating upload URL for {file_name}: {str(e)}",
                status_code=500,
                status="error"
            )

    return Response(
        message=f"Generated {len(result)} upload URLs successfully",
        status_code=200,
        status="success",
        data={
            "urls": result
        }
    )


def sanitize_filename(filename: str) -> str:
    """
    ทำความสะอาดชื่อไฟล์เพื่อป้องกันปัญหาในการอัพโหลดและการเข้าถึง
    """
    if not filename:
        return "unnamed_file"

    # แยกนามสกุลไฟล์
    name_parts = filename.rsplit('.', 1)
    name = name_parts[0]
    extension = name_parts[1] if len(name_parts) > 1 else ""

    # แทนที่ช่องว่างด้วย underscore
    name = name.replace(' ', '_')

    # กรองอักขระพิเศษที่อาจมีปัญหา
    # เก็บเฉพาะตัวอักษร ตัวเล�� _ และ -
    import re
    name = re.sub(r'[^\w\-]', '', name)

    # จำกัดความยาวชื่อไฟล์
    max_length = 200  # รวมนามสกุล
    if len(name) + len(extension) + 1 > max_length:
        name = name[:max_length - len(extension) - 1]

    # รวมนามสกุลกลับเข้าไป
    if extension:
        return f"{name}.{extension}"
    return name


@router.post("/process-uploaded-images", response_model=Response)
async def process_uploaded_images(
        request: dict,
        background_tasks: BackgroundTasks,
        current_user: User = Depends(get_current_active_user),
        db: Session = Depends(get_db)
):
    event_id = request.get("eventId")
    images = request.get("images", [])

    if not event_id:
        return Response(
            message="ต้องระบุ Event ID",
            status_code=400,
            status="error"
        )

    if not images:
        return Response(
            message="ไม่มีรูปภาพที่ระบุ",
            status_code=400,
            status="error"
        )

    event = db.query(Event).filter(
        Event.id == event_id,
        Event.user_id == current_user.id
    ).first()

    if not event:
        return Response(
            message="ไม่พบอีเวนต์หรือไม่มีสิทธิ์เข้าถึง",
            status_code=404,
            status="error"
        )

    # บันทึกข้อมูลรูปภาพลง DB
    image_records = await save_images_to_database(images, event_id, current_user.id, db)

    # สร้าง URL preview สำหรับแต่ละรูปภาพ
    preview_urls = []
    for image_record in image_records:
        file_path = image_record.get("file_path")
        file_name = image_record.get("file_name")
        photo_id = image_record.get("photo_id")

        # สร้าง URL สำหรับรูปภาพ preview
        preview_url = generate_presigned_url(f"{file_path}preview/{file_name}")

        preview_urls.append({
            "id": photo_id,
            "file_name": file_name,
            "preview_url": preview_url,
            "uploaded_at": image_record.get("uploaded_at", datetime.utcnow().isoformat())
        })

    # เริ่มการตรวจจับใบหน้าในพื้นหลัง
    background_tasks.add_task(
        process_face_detection_background,
        image_records=image_records,
        event_id=event_id,
        db_session_maker=SessionLocal
    )

    # ส่งการตอบกลับพร้อม URL preview
    return Response(
        message="บันทึกรูปภาพเรียบร้อยและเริ่มการตรวจจับใบหน้าในพื้นหลัง",
        status_code=200,
        status="success",
        data={
            "total_images": len(images),
            "processing_faces": True,
            "preview_images": preview_urls
        }
    )


# ส่วนแรก: อัพเดทข้อมูลรูปภาพลงฐานข้อมูลทันที
async def save_images_to_database(images: list, event_id: int, user_id: int, db: Session):
    """บันทึกข้อมูลรู���ภาพลงฐานข้อมูลทันที หลังจากอัพโหลด"""
    results = []

    for image in images:
        try:
            file_name = image.get("name")
            file_path = f"{user_id}/{event_id}/"

            # สร้าง record ในฐานข้อมูล
            new_photo = Photo(
                file_name=file_name,
                file_path=file_path,
                size=image.get("size", 0),
                is_detected_face=False,  # ยังไม่ได้ตรวจจับใบหน้า
            )

            db.add(new_photo)
            db.flush()  # ให้ได้ ID ก่อนที่จะ commit

            # เพิ่มความสัมพันธ์กับ event
            event_photo = EventPhoto(
                event_id=event_id,
                photo_id=new_photo.id
            )
            db.add(event_photo)

            # เก็บข้อมูลสำหรับการประมวลผลใบหน้าในภายหลัง
            results.append({
                "photo_id": new_photo.id,
                "file_name": file_name,
                "file_path": file_path
            })
        except Exception as e:
            logger.error(f"เกิดข้อผิดพลาดในการบันทึกรูปภาพ {file_name}: {str(e)}")

    # อัพเดทข้อมูล event
    event = db.query(Event).filter(Event.id == event_id).first()
    event.total_image_count += len(results)

    db.commit()

    return results


# ส่วนที่สอง: ตรวจจับใบหน้าในพื้นหลัง
async def process_face_detection_background(image_records: list, event_id: int, db_session_maker):
    """ประมวลผลการตรวจจับใบหน้าในพื้นหลังด้วย insightface"""
    with db_session_maker() as db:
        try:
            event = db.query(Event).filter(Event.id == event_id).first()
            event.is_processing_face_detection = True
            db.commit()

            s3_client = boto3.client('s3',
                                    aws_access_key_id=settings.SPACES_ACCESS_KEY_ID,
                                    aws_secret_access_key=settings.SPACES_SECRET_ACCESS_KEY,
                                    endpoint_url=settings.SPACES_ENDPOINT)

            for image_record in image_records:
                try:
                    photo_id = image_record["photo_id"]
                    file_path = f"{image_record['file_path']}{image_record['file_name']}"

                    # ดึงรูปภาพจาก DigitalOcean Spaces
                    response = s3_client.get_object(Bucket='snapgoated', Key=file_path)
                    image_data = response['Body'].read()

                    # ตรวจจับใบหน้าด้วย insightface แทน dlib
                    with io.BytesIO(image_data) as image_bytes:
                        vectors = await detect_faces_with_insightface(image_bytes, False)

                    # อัพเดทฐานข้อมูล
                    photo = db.query(Photo).filter(Photo.id == photo_id).first()
                    if photo and vectors is not None:
                        photo.is_detected_face = True
                        db.commit()

                        # บันทึกเวกเตอร์ใบหน้า
                        for vector in vectors:
                            if isinstance(vector, np.ndarray):
                                vector = vector.astype(np.float32)  # ทำให้แน่ใจว่า vector อยู่ในรูปแบบ float32
                            insert_face_vector(db, photo_id, json.dumps(vector.tolist() if isinstance(vector, np.ndarray) else vector))

                        logger.info(f"ตรวจพบใบหน้าในรูปภาพ ID {photo_id}")

                except Exception as e:
                    logger.error(f"เกิดข้อผิดพลาดในการตรวจจับใบหน้าสำหรับรูปภาพ ID {image_record.get('photo_id')}: {str(e)}")

            # อัพเดทสถานะ event เมื่อเสร็จสิ้น
            event = db.query(Event).filter(Event.id == event_id).first()
            event.is_processing_face_detection = False
            db.commit()

        except Exception as e:
            logger.error(f"เกิดข้อผ��ดพลาดในการประมวลผลใบหน้าในพื้นหลัง: {str(e)}")
            try:
                # พยายามอัพเดทสถานะกลับเป็น false
                event = db.query(Event).filter(Event.id == event_id).first()
                event.is_processing_face_detection = False
                db.commit()
            except Exception as inner_e:
                logger.error(f"ไม่สามารถรีเซ็ตสถานะการประมวลผล: {str(inner_e)}")

async def process_images_in_background(
        images: list,
        event_id: int,
        user_id: int,
        db_session_maker
):
    with db_session_maker() as db:
        try:
            event = db.query(Event).filter(Event.id == event_id).first()
            event.is_processing_face_detection = True
            db.commit()

            current_user = db.query(User).filter(User.id == user_id).first()

            s3_client = boto3.client('s3',
                                     aws_access_key_id=settings.SPACES_ACCESS_KEY_ID,
                                     aws_secret_access_key=settings.SPACES_SECRET_ACCESS_KEY,
                                     endpoint_url=settings.SPACES_ENDPOINT)

            processed_results = []

            for image in images:
                file_name = image.get("name")
                is_preview = image.get("isPreviewFile", False)
                base_path = f"{current_user.id}/{event_id}"
                original_key = f"{base_path}/{file_name}"
                preview_key = f"{base_path}/preview/{file_name}"

                try:
                    if is_preview:
                        try:
                            s3_client.head_object(Bucket='snapgoated', Key=preview_key)
                        except Exception as e:
                            processed_results.append({
                                "name": file_name,
                                "success": False,
                                "error": f"ไม่พบไฟล์พรีวิว: {str(e)}"
                            })
                            continue

                        image_obj = io.BytesIO()
                        s3_client.download_fileobj('snapgoated', preview_key, image_obj)

                        face_vectors = await detect_faces_with_insightface(image_obj, is_main_face=False, max_faces=20)

                    else:

                        try:
                            s3_client.head_object(Bucket='snapgoated', Key=original_key)
                        except Exception as e:
                            processed_results.append({
                                "name": file_name,
                                "success": False,
                                "error": f"ไม่พบไฟล์ต้นฉบับ: {str(e)}"
                            })
                            continue

                        image_obj = io.BytesIO()
                        s3_client.download_fileobj('snapgoated', original_key, image_obj)

                        try:
                            try:
                                s3_client.head_object(Bucket='snapgoated', Key=preview_key)
                                preview_exists = True
                            except:
                                preview_exists = False

                            if not preview_exists:
                                image_obj.seek(0)
                                with Image.open(image_obj) as img:
                                    img = img.convert('RGB')
                                    max_size = (800, 800)
                                    img.thumbnail(max_size, Image.Resampling.LANCZOS)

                                    preview_obj = io.BytesIO()
                                    img.save(preview_obj, format='JPEG', quality=85)
                                    preview_obj.seek(0)

                                    s3_client.upload_fileobj(preview_obj, 'snapgoated', preview_key)

                            image_obj.seek(0)
                            face_vectors = await detect_faces_with_insightface(image_obj, is_main_face=False,
                                                                               max_faces=5)

                        except Exception as e:
                            processed_results.append({
                                "name": file_name,
                                "success": False,
                                "error": f"เกิดข้อผิดพลาดในการประมวลผลภาพ: {str(e)}"
                            })
                            continue

                    photo = db.query(Photo).filter(
                        Photo.file_name == file_name,
                        Photo.file_path == f"{base_path}/"
                    ).first()

                    if not photo:
                        photo = Photo(
                            file_name=file_name,
                            file_path=f"{base_path}/",
                            uploaded_at=datetime.utcnow(),
                            is_detected_face=True if face_vectors else False,
                            is_face_verified=True
                        )
                        db.add(photo)
                        db.flush()

                        event_photo = EventPhoto(
                            event_id=event_id,
                            photo_id=photo.id
                        )
                        db.add(event_photo)

                        try:
                            obj = s3_client.head_object(Bucket='snapgoated', Key=original_key)
                            file_size = obj.get('ContentLength', 0)
                            event = db.query(Event).filter(Event.id == event_id).first()
                            event.total_image_count += 1
                            event.total_image_size += file_size
                        except Exception as e:
                            logger.error(f"เกิดข้อผิดพลาดในการดึงขนาดไฟล์: {str(e)}")
                    else:
                        photo.is_detected_face = True if face_vectors else False
                        photo.is_face_verified = True

                    if face_vectors and len(face_vectors) > 0:
                        for vector in face_vectors:
                            if isinstance(vector, np.ndarray) and len(vector) == 512:
                                face_vector = PhotoFaceVector(
                                    photo_id=photo.id,
                                    vector=vector.tolist()
                                )
                                db.add(face_vector)
                            else:
                                logger.error(
                                    f"ข้ามเวกเตอร์ที่มีขนาดไม่ถูกต้อง: {len(vector) if hasattr(vector, '__len__') else 'unknown'}")

                    db.commit()

                    processed_results.append({
                        "name": file_name,
                        "success": True,
                        "has_faces": bool(face_vectors),
                        "face_count": len(face_vectors) if face_vectors else 0
                    })

                except Exception as e:
                    db.rollback()
                    processed_results.append({
                        "name": file_name,
                        "success": False,
                        "error": str(e)
                    })
                    logger.error(f"เกิดข้อผิดพลาดในการประมวลผลรูปภาพ {file_name}: {str(e)}")

            event = db.query(Event).filter(Event.id == event_id).first()
            event.is_processing_face_detection = False
            db.commit()

            logger.info(
                f"การประมวลผลภาพเสร็จสิ้น สำหรับอีเวนต์ {event_id}: สำเร็จ {sum(1 for r in processed_results if r.get('success'))} จาก {len(images)}")

        except Exception as e:
            logger.error(f"เกิดข้อผิดพลาดในการประมวลผลภาพในเบื้องหลัง: {str(e)}")
            try:
                with db_session_maker() as db:
                    event = db.query(Event).filter(Event.id == event_id).first()
                    if event:
                        event.is_processing_face_detection = False
                        db.commit()
            except Exception as inner_e:
                logger.error(f"เกิดข้อผิดพลาดในการรีเซ็ตสถานะการประมวลผล: {str(inner_e)}")

async def process_files_in_background(
        files: List[UploadFile],
        event_id: int,
        current_user: User,
        db_session: Session,
        folder_id: Optional[int],
        batch_size: int,
        progress_logger: UploadProgressLogger,
        connection_id: str
):
    websocket = active_connections.get(connection_id)

    with SessionLocal() as session:
        event = session.query(Event).filter(Event.id == event_id).first()
        if not event:
            print(f"Event {event_id} not found")
            return

    try:
        print(f"Starting background processing of {len(files)} files for event {event_id}")
        
        # Calculate optimal batch size based on available memory
        available_memory = psutil.virtual_memory().available
        optimal_batch_size = min(batch_size, max(1, available_memory // (100 * 1024 * 1024)))  # 100MB per file estimate
        
        for i in range(0, len(files), optimal_batch_size):
            batch = files[i:i + optimal_batch_size]
            tasks = []
            
            # Process files in parallel with rate limiting
            semaphore = asyncio.Semaphore(3)  # Limit concurrent processing
            
            async def process_with_semaphore(file):
                async with semaphore:
                    with SessionLocal() as file_db:
                        return await process_single_file_insightface(
                            file, event_id, current_user, file_db, folder_id,
                            progress_logger, websocket, event
                        )
            
            for file in batch:
                tasks.append(process_with_semaphore(file))
            
            # Wait for all tasks in current batch to complete
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Process results and update progress
            for idx, result in enumerate(batch_results):
                file_index = i + idx
                if file_index < len(files):
                    filename = files[file_index].filename
                    if isinstance(result, Exception):
                        print(f"❌ Failed to upload {filename}: {str(result)}")
                    else:
                        print(f"✅ Successfully processed {filename} ({file_index + 1}/{len(files)})")
            
            # Add small delay between batches to prevent overwhelming resources
            await asyncio.sleep(0.5)
            
            # Force garbage collection after each batch
            gc.collect()
            
            # Update event timestamp periodically
            if i % (optimal_batch_size * 5) == 0:
                with SessionLocal() as update_db:
                    event_update = update_db.query(Event).filter(Event.id == event_id).first()
                    if event_update:
                        event_update.updated_at = datetime.utcnow()
                        update_db.commit()

        # Final event update
        with SessionLocal() as final_db:
            event_update = final_db.query(Event).filter(Event.id == event_id).first()
            if event_update:
                event_update.updated_at = datetime.utcnow()
                final_db.commit()

        if websocket and not websocket.client_disconnected:
            await send_upload_progress(
                websocket,
                f"Completed processing {progress_logger.successful_files}/{len(files)} files",
                progress_logger,
                {"completed": True}
            )
    except Exception as e:
        print(f"Background processing error: {str(e)}")
        if websocket and not websocket.client_disconnected:
            await send_upload_progress(
                websocket,
                "Error in background processing",
                progress_logger,
                {"error": str(e)},
                "error"
            )
    finally:
        # Clean up resources
        for file in files:
            if not file.file.closed:
                await file.close()
        gc.collect()


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
                    preview_path = f"{file_path}/preview/{file_name}"
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
            "preview_url": generate_presigned_url(f"{new_photo.file_path}/preview/{new_photo.file_name}")
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


async def process_single_file_insightface(
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
                raise HTTPException(status_code=404, detail="ไม่พบโฟลเดอร์")
            file_path += f"/{event_folder.name}"

        file_name = check_duplicate_name(file.filename, file_path, False)
        full_path = f"{file_path}/{file_name}"

        # Read file content in chunks to manage memory
        chunk_size = 1024 * 1024  # 1MB chunks
        file_content = io.BytesIO()
        total_size = 0
        
        while chunk := await file.read(chunk_size):
            file_content.write(chunk)
            total_size += len(chunk)
        
        file_content.seek(0)

        # Upload original file
        upload_files_to_spaces(file_content, full_path)

        # Create and upload preview
        try:
            preview_bytes = io.BytesIO()
            with Image.open(file_content) as image:
                image = image.convert("RGB")
                max_size = (image.width // 2, image.height // 2)
                image.thumbnail(max_size, Image.LANCZOS)
                image.save(preview_bytes, format="WEBP", quality=50, optimize=True)
                preview_bytes.seek(0)
                preview_path = f"{file_path}/preview/{file_name}"
                upload_files_to_spaces(preview_bytes, preview_path)
        except Exception as e:
            logger.error(f"Error creating preview for {file_name}: {str(e)}")

        # Face detection with memory optimization
        try:
            face_bytes = io.BytesIO()
            file_content.seek(0)
            face_bytes.write(file_content.getvalue())
            face_bytes.seek(0)
            vectors = await detect_faces_with_insightface(face_bytes, False)
        except Exception as e:
            logger.error(f"Error detecting faces for {file_name}: {str(e)}")
            vectors = None

        # Database operations
        new_photo = Photo(
            file_name=file_name,
            file_path=f"{file_path}/",
            size=total_size,
            is_detected_face=(vectors is not None),
        )
        db.add(new_photo)
        db.commit()
        db.refresh(new_photo)

        if vectors is not None:
            for vector in vectors:
                if isinstance(vector, np.ndarray):
                    vector = vector.astype(np.float32)
                vector_json = json.dumps(vector.tolist() if isinstance(vector, np.ndarray) else vector)
                insert_face_vector(db, new_photo.id, vector_json)
            db.commit()

        if folder_id:
            event_folder_photo = EventFolderPhoto(
                event_folder_id=folder_id,
                photo_id=new_photo.id
            )
            db.add(event_folder_photo)
            event_folder.total_photo_count += 1
            event_folder.total_photo_size += total_size
            event_folder.updated_at = datetime.utcnow()
        else:
            event_photo = EventPhoto(
                event_id=event_id,
                photo_id=new_photo.id
            )
            db.add(event_photo)

        event.total_image_count += 1
        event.total_image_size += total_size
        event.updated_at = datetime.utcnow()
        db.commit()

        progress_logger.processed_files += 1
        progress_logger.successful_files += 1

        if websocket:
            await send_upload_progress(
                websocket,
                f"ประมวลผล {file_name} เสร็จสิ้น",
                progress_logger,
                {"file_name": file_name}
            )

        return {
            "photo_id": new_photo.id,
            "uploaded_at": new_photo.uploaded_at.isoformat(),
            "file_name": new_photo.file_name,
            "preview_url": generate_presigned_url(f"{new_photo.file_path}/preview/{new_photo.file_name}")
        }

    except Exception as e:
        progress_logger.processed_files += 1
        progress_logger.failed_files += 1
        if websocket:
            await send_upload_progress(
                websocket,
                f"ล้มเหลวในการประมวลผล {getattr(file, 'filename', 'unknown')}",
                progress_logger,
                {"error": str(e)},
                "error"
            )
        logger.error(f"เกิดข้อผิดพลาดในการประมวลผลไฟล์ {getattr(file, 'filename', 'unknown')}: {str(e)}")
        return None
    finally:
        # Clean up resources
        if not file.file.closed:
            await file.close()
        gc.collect()

@router.websocket("/ws/upload-progress/{event_id}")
async def upload_progress_websocket(
        websocket: WebSocket,
        event_id: int,
        current_user: User = Depends(get_ws_current_active_user)
):
    connection_id = f"{event_id}_{current_user.id}"
    try:
        await websocket.accept()
        active_connections[connection_id] = websocket

        # Send initial connection status
        await websocket.send_json({
            "type": "connected",
            "message": "Upload progress connection established",
            "event_id": event_id,
            "timestamp": datetime.utcnow().isoformat()
        })

        # Set up heartbeat to keep connection alive
        heartbeat_task = asyncio.create_task(send_heartbeat(websocket))
        
        try:
            while True:
                try:
                    # Set timeout for receiving messages
                    data = await asyncio.wait_for(websocket.receive_json(), timeout=30)
                    
                    if data.get("type") == "upload_complete":
                        await websocket.send_json({
                            "type": "upload_status",
                            "status": "completed",
                            "message": "All files uploaded successfully",
                            "timestamp": datetime.utcnow().isoformat()
                        })
                        break
                    elif data.get("type") == "ping":
                        await websocket.send_json({
                            "type": "pong",
                            "timestamp": datetime.utcnow().isoformat()
                        })
                        
                except asyncio.TimeoutError:
                    # Send ping to check if client is still alive
                    await websocket.send_json({
                        "type": "ping",
                        "timestamp": datetime.utcnow().isoformat()
                    })
                    
        except WebSocketDisconnect:
            logger.info(f"Client disconnected: {connection_id}")
        finally:
            # Clean up
            heartbeat_task.cancel()
            if connection_id in active_connections:
                del active_connections[connection_id]
                logger.info(f"Cleaned up connection: {connection_id}")

    except Exception as e:
        logger.error(f"WebSocket error: {str(e)}")
        try:
            await websocket.close(code=1011)
        except:
            pass
        if connection_id in active_connections:
            del active_connections[connection_id]

async def send_heartbeat(websocket: WebSocket):
    """Send periodic heartbeat to keep connection alive"""
    try:
        while True:
            await asyncio.sleep(30)  # Send heartbeat every 30 seconds
            if not websocket.client_disconnected:
                await websocket.send_json({
                    "type": "heartbeat",
                    "timestamp": datetime.utcnow().isoformat()
                })
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"Heartbeat error: {str(e)}")

async def send_upload_progress(
    websocket: WebSocket,
    message: str,
    progress: UploadProgressLogger,
    data: dict = None,
    level: str = "info"
):
    """Send formatted progress update through WebSocket with error handling"""
    try:
        if websocket.client_disconnected:
            return
            
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
        # Don't raise the exception to prevent disrupting the upload process


def cleanup_orphaned_files():
    """
    ตรวจสอบและลบไฟล์ที่ไม่���ีในฐานข้อมูลออกจาก DigitalOcean Spaces
    รันทุกวั���ตอนเที่ยงคืน
    """
    logger.info("เริ่มต้นการทำความสะอาดไฟล์ที่ไม่มีในฐานข้อมูล")

    # เชื่อมต่อกับ DigitalOcean Spaces
    s3_client = boto3.client('s3',
                             aws_access_key_id=settings.SPACES_ACCESS_KEY_ID,
                             aws_secret_access_key=settings.SPACES_SECRET_ACCESS_KEY,
                             endpoint_url=settings.SPACES_ENDPOINT)

    # ดึงรายการไฟล์ทั้งหมด (อาจต้องใช้ pagination หากมีไฟล์จำนวนมาก)
    try:
        paginator = s3_client.get_paginator('list_objects_v2')
        pages = paginator.paginate(Bucket='snapgoated')

        deleted_count = 0
        checked_count = 0

        # ตรวจสอบทีละหน้า
        for page in pages:
            if 'Contents' not in page:
                continue

            for obj in page['Contents']:
                key = obj['Key']
                checked_count += 1

                # ข้ามไฟล์ที่อยู่ในโฟลเดอร์ preview หรือ settings
                if '/preview/' in key or '/settings/' in key:
                    continue

                # แยกเส้นทางและชื่อไฟล์
                path_parts = key.split('/')
                if len(path_parts) < 2:
                    continue

                file_name = path_parts[-1]
                file_path = '/'.join(path_parts[:-1]) + '/'

                # ตรวจสอบในฐานข้อมูล
                with SessionLocal() as db:
                    db_file = db.query(Photo).filter(
                        Photo.file_name == file_name,
                        Photo.file_path == file_path
                    ).first()

                    if not db_file:
                        # ไฟล์ไม่มีในฐานข้อมูล ให้ลบทั้งไฟล์ต้นฉบับและพรีวิว
                        try:
                            # ลบไฟล์ต้นฉบับ
                            s3_client.delete_object(Bucket='snapgoated', Key=key)

                            # ลบไฟล์พรีวิว
                            preview_key = f"{file_path}preview/{file_name}"
                            s3_client.delete_object(Bucket='snapgoated', Key=preview_key)

                            deleted_count += 1
                            logger.info(f"ลบไฟล์: {key} และ {preview_key}")

                            if deleted_count % 100 == 0:
                                logger.info(f"ลบไฟล์ไปแล้ว {deleted_count} รายการ")

                        except Exception as e:
                            logger.error(f"เกิดข้อผิดพลาดในการลบไฟล์ {key}: {str(e)}")

        logger.info(
            f"การทำความสะอาดเสร็จสิ้น: ตรวจสอบไปแล้ว {checked_count} รายการ, ลบไปทั้งหมด {deleted_count} รายการ")
        return {
            "success": True,
            "checked_files": checked_count,
            "deleted_files": deleted_count,
            "timestamp": datetime.utcnow().isoformat()
        }

    except Exception as e:
        logger.error(f"เกิดข้อผิดพลาดในการทำความสะอาดไฟล์: {str(e)}")
        return {
            "success": False,
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat()
        }





