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
from app.db.models.PhotoVector import PhotoVector
from app.db.queries.image_queries import insert_face_vector
from app.db.session import get_db
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
                f"{photo.file_path}/preview_{photo.file_name}")
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

    # Delete cover photo if it exists
    if event.cover_photo_id:
        cover_photo = db.query(Photo).filter(Photo.id == event.cover_photo_id).first()
        if cover_photo:
            delete_file_from_spaces(f"{cover_photo.file_path}{cover_photo.file_name}")
            delete_file_from_spaces(f"{cover_photo.file_path}preview_{cover_photo.file_name}")
            db.delete(cover_photo)
            db.commit()

    # Delete all photos in event
    photos = db.query(Photo).join(EventPhoto).filter(EventPhoto.event_id == event_id).all()
    for photo in photos:
        delete_file_from_spaces(f"{photo.file_path}{photo.file_name}")
        delete_file_from_spaces(f"{photo.file_path}preview_{photo.file_name}")

        # Delete photo vectors
        db.query(PhotoVector).filter(PhotoVector.photo_id == photo.id).delete()
        db.commit()

        # Delete from EventPhoto
        db.query(EventPhoto).filter(EventPhoto.photo_id == photo.id).delete()
        db.commit()

        # Delete photo from database
        db.delete(photo)
        db.commit()

    # Delete all folders in event
    folders = db.query(EventFolder).filter(EventFolder.event_id == event_id).all()
    for folder in folders:
        photos = db.query(Photo).join(EventFolderPhoto).filter(EventFolderPhoto.event_folder_id == folder.id).all()
        for photo in photos:
            delete_file_from_spaces(f"{photo.file_path}{photo.file_name}")
            delete_file_from_spaces(f"{photo.file_path}preview_{photo.file_name}")

            # Delete photo vectors
            db.query(PhotoVector).filter(PhotoVector.photo_id == photo.id).delete()
            db.commit()

            # Delete from EventFolderPhoto
            db.query(EventFolderPhoto).filter(
                EventFolderPhoto.photo_id == photo.id,
                EventFolderPhoto.event_folder_id == folder.id
            ).delete()
            db.commit()

            # Delete photo from database
            db.delete(photo)
            db.commit()

        db.delete(folder)
        db.commit()

    db.delete(event)
    db.commit()

    return Response(
        message="Event deleted successfully",
        status="success",
        status_code=200
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
    try:
        while True:
            try:
                logger.info("Waiting for data...")
                data = await asyncio.wait_for(websocket.receive_text(), timeout=300)
                logger.info(f"Received data: {data}")
                message = json.loads(data)
                message_type = message.get('type')

                if message_type == "upload_file":
                    logger.info("Received upload file message.")
                    if 'file_name' in message and 'file_data' in message:
                        if message['file_name'] == "END":
                            logger.info("Received END signal, stopping upload.")
                            break
                    logger.info("Processing file upload.")
                    file_path = f"{current_user.id}/{event_id}"
                    logger.info(f"File path: {file_path}")
                    file_name = check_duplicate_name(message['file_name'], file_path, False)
                    logger.info(f"File name: {file_name}")
                    full_path = f"{current_user.id}/{event_id}/{file_name}"
                    logger.info(f"Full path: {full_path}")
                    file_data = base64.b64decode(message['file_data'])
                    file_bytes = io.BytesIO(file_data)

                    logger.info(f"Uploading file {file_name} to {full_path}.")
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
                        preview_path = f"{current_user.id}/{event_id}/preview_{file_name}"
                        preview_bytes.seek(0)

                        logger.info(f"Uploading preview for {file_name} to {preview_path}.")
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
                        logger.info(f"Photo {file_name} saved to database with ID {new_photo.id}.")

                        if vectors is not None:
                            for vector in vectors:
                                if isinstance(vector, np.ndarray):
                                    vector = vector.tolist()
                                insert_face_vector(db, new_photo.id, json.dumps(vector))
                            logger.info(f"Face vectors for photo {file_name} inserted into database.")

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
                        logger.info(f"Event {event_id} updated with new photo {file_name}.")
                    except Exception as e:
                        db.rollback()
                        logger.error(f"Error saving photo {file_name} to database: {str(e)}")
                        await websocket.send_json({
                            "message": f"Error saving photo to database: {str(e)}",
                            "status": "error",
                            "status_code": 500
                        })
                        await websocket.close(code=1011)
                        break

                    await websocket.send_json({
                        "message": f"File {file_name} uploaded successfully",
                        "status": "success",
                        "status_code": 200,
                        "data": {
                            "photo_id": new_photo.id,
                            "uploaded_at": new_photo.uploaded_at.isoformat(),
                            "file_name": new_photo.file_name,
                            "preview_url": generate_presigned_url(
                                f"{new_photo.file_path}/preview_{new_photo.file_name}")
                        }
                    })
                    logger.info(f"File {file_name} upload process completed successfully.")
                elif message_type == "upload_file_in_folder":
                    if 'file_name' in message and 'file_data' in message and 'folder_id' in message:
                        if message['file_name'] == "END":
                            break

                    event_folder = db.query(EventFolder).filter(EventFolder.event_id == event_id,
                                                          EventFolder.id == message['folder_id']).first()
                    if not event_folder:
                        await websocket.send_json({
                            "message": "Folder not found: " + str(message['folder_id']),
                            "status": "error",
                            "status_code": 404
                        })
                        await websocket.close(code=1008)
                        return

                    file_path = f"{current_user.id}/{event_id}/{event_folder.name}"
                    file_name = check_duplicate_name(message['file_name'], file_path, False)
                    full_path = f"{current_user.id}/{event_id}/{event_folder.name}/{file_name}"
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
                        image.save(preview_bytes, format="WEBP", quality=50, optimize=True)  # ลด quality ลงให้มากขึ้น
                        preview_path = f"{current_user.id}/{event_id}/{event_folder.name}/preview_{file_name}"
                        preview_bytes.seek(0)

                        upload_files_to_spaces(preview_bytes, preview_path)

                    face_detected_bytes = io.BytesIO(file_data)
                    vectors = await detect_faces_with_dlib_in_event(face_detected_bytes, False)

                    # Save to database
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
                        await websocket.send_json(
                            {
                                "message": f"Error saving photo to database: {str(e)}",
                                "status": "error",
                                "status_code": 500
                            }
                        )
                        await websocket.close(code=1011)
                        break

                    await websocket.send_json({
                        "message": f"File {file_name} uploaded successfully",
                        "status": "success",
                        "status_code": 200,
                        "data": {
                            "photo_id": new_photo.id,
                            "uploaded_at": new_photo.uploaded_at.isoformat(),
                            "folder_name": event_folder.name,
                            "file_name": new_photo.file_name,
                            "preview_url": generate_presigned_url(
                                f"{new_photo.file_path}/preview_{new_photo.file_name}")
                        }
                    })
                elif message_type == "create_folder":
                    folder_path = f"{current_user.id}/{event_id}"
                    folder_name = check_duplicate_name(f"{message['folder_name']}/", folder_path, True)
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
                        break

                    await websocket.send_json(
                        {
                            "message": f"Folder {folder_name} created successfully",
                            "status": "success",
                            "status_code": 200,
                            "data": {
                                "folder_id": event_folder.id,
                                "folder_name": event_folder.name
                            }
                        }
                    )
                elif message_type == "delete_file":
                    if 'file_id' in message:
                        file_id = message['file_id']
                        photo = db.query(Photo).join(EventPhoto).filter(
                            Photo.id == file_id,
                            EventPhoto.event_id == event_id
                        ).first()
                        if not photo:
                            return await websocket.send_json({
                                "message": "File not found",
                                "status": "error",
                                "status_code": 404,
                                "data": {"file_id": file_id}
                            })

                        delete_file_from_spaces(f"{photo.file_path}{photo.file_name}")
                        delete_file_from_spaces(f"{photo.file_name}preview_{photo.file_name}")

                        # Update event file size and count
                        event.total_image_count -= 1
                        event.total_image_size -= photo.size
                        db.commit()

                        # Delete photo vectors
                        db.query(PhotoVector).filter(PhotoVector.photo_id == photo.id).delete()
                        db.commit()

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
                elif message_type == "delete_file_in_folder":
                    if 'file_id' in message and 'folder_id' in message:
                        file_id = message['file_id']
                        folder_id = message['folder_id']
                        photo = db.query(Photo).join(EventPhoto).join(EventFolderPhoto).filter(
                            Photo.id == file_id,
                            EventPhoto.event_id == event_id,
                            EventFolderPhoto.event_folder_id == folder_id
                        ).first()
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
                        event.total_image_count -= 1
                        event.total_image_size -= photo.size
                        db.commit()

                        # Update event folder file size and count
                        event_folder = db.query(EventFolder).filter(EventFolder.id == folder_id).first()
                        if event_folder:
                            event_folder.total_photo_count -= 1
                            event_folder.total_photo_size -= photo.size
                            db.commit()

                        # Delete photo vectors
                        db.query(PhotoVector).filter(PhotoVector.photo_id == photo.id).delete()
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

                        await websocket.send_json({
                            "message": f"File {photo.file_name} deleted successfully",
                            "status": "success",
                            "status_code": 200,
                            "data": {"file_id": photo.id}
                        })
                elif message_type == "delete_folder":
                    if 'folder_id' in message:
                        folder_id = message['folder_id']
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
                            event.total_image_count -= 1
                            event.total_image_size -= photo.size
                            db.commit()

                            # Delete photo vectors
                            db.query(PhotoVector).filter(PhotoVector.photo_id == photo.id).delete()
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

            except asyncio.TimeoutError:
                await websocket.send_json({
                    "message": "No activity for 5 minutes, closing connection.",
                    "status": "error",
                    "status_code": 408
                })
                await websocket.close(code=1000)
                break

    except WebSocketDisconnect:
        print("Client disconnected")
    except Exception as e:
        logger.error(f"Error during file upload: {str(e)}")
        await websocket.send_json({
            "message": f"Error during file upload: {str(e)}",
            "status": "error",
            "status_code": 500
        })
        await websocket.close(code=1011)

