import asyncio
import base64
import io
import json
import logging
from datetime import datetime

import numpy as np
from PIL import Image
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, HTTPException, Query, UploadFile, File, Form
from fastapi.encoders import jsonable_encoder
from sqlalchemy import false
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.db.models.Country import Country
from app.db.models.EventCreditType import EventCreditType
from app.db.models.EventFolder import EventFolder
from app.db.models.EventFolderPhoto import EventFolderPhoto
from app.db.models.EventPhoto import EventPhoto
from app.db.models.EventType import EventType
from app.db.queries.image_queries import insert_face_vector
from app.db.session import get_db
from app.schemas.event import Event as EventSchema, EventCreate, Credit
from app.security.auth import get_current_active_user, get_ws_current_active_user
from typing import List, Dict, Any, Optional

from app.services.digital_oceans import upload_file_to_spaces, generate_presigned_url, create_folder_in_spaces, \
    check_duplicate_name, upload_files_to_spaces

from app.db.models.User import User
from app.db.models.Photo import Photo
from app.db.models.Event import Event
from app.db.models.EventCredit import EventCredit
from app.utils.model.face_detect import detect_faces_with_dlib_in_event
from app.utils.validation import validate_date_format

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/events", response_model=Dict[str, Any])
def get_events(
    page: int = 1,
    limit: int = 10,
    status: Optional[bool] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    if page < 1:
        raise HTTPException(status_code=400, detail="Page number must be greater than 0")

    query = db.query(Event).filter(Event.user_id == current_user.id)

    if status is not None:
        query = query.filter(Event.status == status)

    if search:
        query = query.filter(
            (Event.event_name.ilike(f"%{search}%")) |
            (Event.location.ilike(f"%{search}%"))
        )

    total_events = query.count()
    skip = (page - 1) * limit
    events = query.order_by(Event.date).offset(skip).limit(limit).all()
    total_pages = (total_events + limit - 1) // limit

    events_data = [
        {
            "id": event.id,
            "event_name": event.event_name,
            "event_type_id": event.event_type_id,
            "date": event.date,
            "location": event.location,
            "status": event.status,
            "user_id": event.user_id,
            "publish_at": event.publish_at,
            "cover_url": generate_presigned_url(event.cover_photo.filename) if event.cover_photo else None
        }
        for event in events
    ]

    return jsonable_encoder({
        "total_events": total_events,
        "total_pages": total_pages,
        "current_page": page,
        "events_per_page": limit,
        "events": events_data
    })

@router.get("/prepare-event-data", response_model=Dict[str, Any])
def prepare_event_data(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    event_types = jsonable_encoder(db.query(EventType).all())
    countries = jsonable_encoder(db.query(Country).all())
    event_credit_types = jsonable_encoder(db.query(EventCreditType).all())

    return {
        "event_types": event_types,
        "countries": countries,
        "event_credit_types": event_credit_types
    }

@router.post("/create-event", response_model=Dict[str, str])
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

        validate_date_format(date)
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
            cover_photo_url = f"https://snapgoated.{settings.SPACES_ENDPOINT}/{file_path}"
            cover_photo_size = len(file_content)
            new_photo = Photo(
                filename=cover_photo_path,
                size=cover_photo_size,
                url=cover_photo_url,
            )
            db.add(new_photo)
            db.commit()
            db.refresh(new_photo)

            # Update event with cover photo id
            new_event.cover_photo_id = new_photo.id
            db.commit()
        except Exception as e:
            db.delete(new_event)
            db.commit()
            logger.error(f"Error uploading cover photo: {e}")
            raise HTTPException(status_code=500, detail=f"{e}")

        # Add credits
        for credit in credit_objects:
            new_credit = EventCredit(
                event_id=new_event.id,
                event_credit_type_id=credit.credit_type_id,
                name=credit.name
            )
            db.add(new_credit)
        db.commit()

        return {"message": "Event created successfully"}
    except Exception as e:
        logger.error(f"Error creating event: {e}")
        raise HTTPException(status_code=500, detail=f"Error creating event: {e}")

@router.get("/event-details", response_model=Dict[str, Any])
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
        raise HTTPException(status_code=400, detail="Page number must be greater than 0")

    event = db.query(Event).filter(Event.id == event_id, Event.user_id == current_user.id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

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
        photo_query = photo_query.filter(Photo.filename.ilike(f"%{search}%"))

    if sort_by == "name":
        if sort_order == "asc":
            photo_query = photo_query.order_by(Photo.filename.asc())
        else:
            photo_query = photo_query.order_by(Photo.filename.desc())
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
            "filename": photo.filename.split('/')[-1],
            "preview_url": generate_presigned_url(
                f"{photo.filename.rsplit('/', 1)[0]}/preview_{photo.filename.split('/')[-1]}")
        }
        for photo in photos
    ]
    total_photo_pages = (total_photos + limit - 1) // limit

    return {
        "total_folders": total_folders,
        "total_folder_pages": total_folder_pages,
        "folders_per_page": limit,
        "event_folders": jsonable_encoder(event_folders),
        "total_photos": total_photos,
        "total_photo_pages": total_photo_pages,
        "photos_per_page": limit,
        "photos": jsonable_encoder(photos_data)
    }

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
        raise HTTPException(status_code=400, detail="Page number must be greater than 0")

    folder = db.query(EventFolder).filter(EventFolder.id == folder_id).first()
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")

    # Query photos related to folder
    photo_query = db.query(Photo).join(EventFolderPhoto).filter(EventFolderPhoto.event_folder_id == folder_id)
    if search:
        photo_query = photo_query.filter(Photo.filename.ilike(f"%{search}%"))

    if sort_by == "name":
        if sort_order == "asc":
            photo_query = photo_query.order_by(Photo.filename.asc())
        else:
            photo_query = photo_query.order_by(Photo.filename.desc())
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
            "filename": photo.filename.split('/')[-1],
            "preview_url": generate_presigned_url(
                f"{photo.filename.rsplit('/', 1)[0]}/preview_{photo.filename.split('/')[-1]}")
        }
        for photo in photos
    ]
    total_photo_pages = (total_photos + limit - 1) // limit

    return {
        "total_photos": total_photos,
        "total_photo_pages": total_photo_pages,
        "photos_per_page": limit,
        "photos": jsonable_encoder(photos_data)
    }

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
    try:
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=300)
                message = json.loads(data)
                message_type = message.get('type')

                if message_type == "upload_file":
                    if 'fileName' in message and 'fileData' in message:
                        if message['fileName'] == "END":
                            break

                    file_path = f"{current_user.id}/{event_id}"
                    file_name = check_duplicate_name(message['fileName'], file_path, False)
                    full_path = f"{current_user.id}/{event_id}/{file_name}"
                    file_data = base64.b64decode(message['fileData'])
                    file_bytes = io.BytesIO(file_data)

                    upload_files_to_spaces(file_bytes, full_path)

                    preview_data = base64.b64decode(message['fileData'])
                    preview_bytes = io.BytesIO(preview_data)

                    preview_bytes.seek(0)
                    with Image.open(preview_bytes) as image:
                        image = image.convert("RGB")
                        max_size = (image.width // 2, image.height // 2)
                        image.thumbnail(max_size, Image.LANCZOS)

                        preview_bytes = io.BytesIO()
                        image.save(preview_bytes, format="WEBP", quality=50, optimize=True)  # ลด quality ลงให้มากขึ้น
                        preview_path = f"{current_user.id}/{event_id}/preview_{file_name}"
                        preview_bytes.seek(0)

                        upload_files_to_spaces(preview_bytes, preview_path)

                    face_detected_bytes = io.BytesIO(file_data)
                    vectors = await detect_faces_with_dlib_in_event(face_detected_bytes, False)

                    # Save to database
                    new_photo = Photo(
                        filename=full_path,
                        size=len(file_data),
                        url=f"https://snapgoated.{settings.SPACES_ENDPOINT}/{full_path}",
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

                        event_photo = EventPhoto(
                            event_id=event_id,
                            photo_id=new_photo.id
                        )
                        db.add(event_photo)
                        db.commit()
                        db.refresh(event_photo)

                        event.total_image_count += 1
                        event.total_image_size += len(file_data)
                        event.updated_at = datetime.utcnow()
                        db.commit()
                    except Exception as e:
                        db.rollback()
                        await websocket.send_text(f"Error saving photo to database: {str(e)}")
                        await websocket.close(code=1011)
                        break

                    await websocket.send_text(f"File {file_name} uploaded successfully")
                elif message_type == "upload_file_in_folder":
                    if 'fileName' in message and 'fileData' in message and 'folderId' in message:
                        if message['fileName'] == "END":
                            break

                    event_folder = db.query(EventFolder).filter(EventFolder.event_id == event_id,
                                                          EventFolder.id == message['folderId']).first()
                    if not event_folder:
                        await websocket.send_text(f"Error: Folder {event_folder.name} does not exist.")
                        await websocket.close(code=1008)
                        return

                    file_path = f"{current_user.id}/{event_id}/{event_folder.name}"
                    file_name = check_duplicate_name(message['fileName'], file_path, False)
                    full_path = f"{current_user.id}/{event_id}/{event_folder.name}/{file_name}"
                    file_data = base64.b64decode(message['fileData'])
                    file_bytes = io.BytesIO(file_data)

                    upload_files_to_spaces(file_bytes, full_path)

                    preview_data = base64.b64decode(message['fileData'])
                    preview_bytes = io.BytesIO(preview_data)

                    preview_bytes.seek(0)
                    with Image.open(preview_bytes) as image:
                        image = image.convert("RGB")
                        max_size = (image.width // 2, image.height // 2)
                        image.thumbnail(max_size, Image.LANCZOS)

                        preview_bytes = io.BytesIO()
                        image.save(preview_bytes, format="WEBP", quality=50, optimize=True)  # ลด quality ลงให้มากขึ้น
                        preview_path = f"{current_user.id}/{event_id}/{event_folder.name}/preview_{file_name}"
                        preview_bytes.seek(0)

                        upload_files_to_spaces(preview_bytes, preview_path)

                    face_detected_bytes = io.BytesIO(file_data)
                    vectors = await detect_faces_with_dlib_in_event(face_detected_bytes, False)

                    # Save to database
                    new_photo = Photo(
                        filename=full_path,
                        size=len(file_data),
                        url=f"https://snapgoated.{settings.SPACES_ENDPOINT}/{full_path}",
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

                        event_folder_photo = EventFolderPhoto(
                            event_folder_id=event_folder.id,
                            photo_id=new_photo.id
                        )
                        db.add(event_folder_photo)
                        db.commit()
                        db.refresh(event_folder_photo)

                        event_folder.total_photo_count += 1
                        event_folder.total_photo_size += len(file_data)
                        event_folder.updated_at = datetime.utcnow()
                        db.commit()
                        event.total_image_count += 1
                        event.total_image_size += len(file_data)
                        event.updated_at = datetime.utcnow()
                        db.commit()
                    except Exception as e:
                        db.rollback()
                        await websocket.send_text(f"Error saving photo to database: {str(e)}")
                        await websocket.close(code=1011)
                        break

                    await websocket.send_text(f"File {file_name} uploaded successfully")
                elif message_type == "create_folder":
                    folder_path = f"{current_user.id}/{event_id}"
                    folder_name = check_duplicate_name(f"{message['folderName']}/", folder_path, True)
                    full_path = f"{current_user.id}/{event_id}/{folder_name}"
                    # Create an empty file to represent the folder
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
                        await websocket.send_text(f"Error saving folder to database: {str(e)}")
                        await websocket.close(code=1011)
                        break

                    await websocket.send_text(f"Folder {folder_name} created successfully")

            except asyncio.TimeoutError:
                await websocket.send_text("No activity for 5 minutes, closing connection.")
                await websocket.close(code=1000)
                break

    except WebSocketDisconnect:
        print("Client disconnected")
    except Exception as e:
        logger.error(f"Error during file upload: {str(e)}")
        await websocket.send_text(f"Error: {str(e)}")
        await websocket.close(code=1011)

