import io
import logging
import re

import boto3
from botocore.exceptions import NoCredentialsError
from fastapi import UploadFile, HTTPException

from app.config.settings import settings

logger = logging.getLogger(__name__)

def upload_file_to_spaces(file: UploadFile, file_path: str):
    s3_client = boto3.client('s3',
                             aws_access_key_id=settings.SPACES_ACCESS_KEY_ID,
                             aws_secret_access_key=settings.SPACES_SECRET_ACCESS_KEY,
                             endpoint_url=settings.SPACES_ENDPOINT)
    try:
        file.file.seek(0)
        s3_client.upload_fileobj(file.file, 'snapgoated', file_path)
        return file_path
    except NoCredentialsError:
        logger.error("Credentials not available")
        raise HTTPException(status_code=500, detail="File upload failed " )

def upload_files_to_spaces(file_obj: io.BytesIO, file_path: str):
    s3_client = boto3.client('s3',
                             aws_access_key_id=settings.SPACES_ACCESS_KEY_ID,
                             aws_secret_access_key=settings.SPACES_SECRET_ACCESS_KEY,
                             endpoint_url=settings.SPACES_ENDPOINT)
    try:
        file_obj.seek(0)
        s3_client.upload_fileobj(file_obj, 'snapgoated', file_path)
        return file_path
    except NoCredentialsError:
        logger.error("Credentials not available")
        raise HTTPException(status_code=500, detail="File upload failed " )

def create_folder_in_spaces(folder_path: str):
    s3_client = boto3.client('s3',
                             aws_access_key_id=settings.SPACES_ACCESS_KEY_ID,
                             aws_secret_access_key=settings.SPACES_SECRET_ACCESS_KEY,
                             endpoint_url=settings.SPACES_ENDPOINT)
    try:
        # Create an empty file to represent the folder
        s3_client.put_object(Bucket='snapgoated', Key=f"{folder_path}/")
        return folder_path
    except NoCredentialsError:
        logger.error("Credentials not available")
        raise HTTPException(status_code=500, detail="Folder creation failed")
    except Exception as e:
        logger.error(f"Error creating folder: {e}")
        raise HTTPException(status_code=500, detail=f"Error creating folder: {e}")


def check_duplicate_name(base_name: str, folder_path: str, is_folder: bool) -> str:
    s3_client = boto3.client('s3',
                             aws_access_key_id=settings.SPACES_ACCESS_KEY_ID,
                             aws_secret_access_key=settings.SPACES_SECRET_ACCESS_KEY,
                             endpoint_url=settings.SPACES_ENDPOINT)
    try:
        existing_files = s3_client.list_objects_v2(Bucket='snapgoated', Prefix=folder_path)
        existing_names = [obj['Key'] for obj in existing_files.get('Contents', [])]

        if is_folder:
            base_name = base_name.rstrip('/') + '/'

        if f"{folder_path}/{base_name}" not in existing_names:
            return base_name.rstrip('/')

        if is_folder:
            name = base_name.rstrip('/')
            ext = ''
        else:
            name, ext = base_name.rsplit('.', 1) if '.' in base_name else (base_name, '')

        counter = 1
        pattern = re.compile(rf"{re.escape(name)} \((\d+)\)\.{re.escape(ext)}" if ext else rf"{re.escape(name)} \((\d+)\)")
        for existing_name in existing_names:
            match = pattern.match(existing_name[len(folder_path) + 1:])
            if match:
                counter = max(counter, int(match.group(1)) + 1)

        new_name = f"{name} ({counter}){'.' + ext if ext else ''}"
        return new_name.rstrip('/')
    except NoCredentialsError:
        raise HTTPException(status_code=500, detail="Credentials not available")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error checking duplicate name: {e}")

def generate_presigned_url(file_path: str, expiration: int = 3600):
    s3_client = boto3.client('s3',
                             aws_access_key_id=settings.SPACES_ACCESS_KEY_ID,
                             aws_secret_access_key=settings.SPACES_SECRET_ACCESS_KEY,
                             endpoint_url=settings.SPACES_ENDPOINT)
    try:
        presigned_url = s3_client.generate_presigned_url('get_object',
                                                         Params={'Bucket': 'snapgoated', 'Key': file_path},
                                                         ExpiresIn=expiration)
        return presigned_url
    except NoCredentialsError:
        raise HTTPException(status_code=500, detail="Credentials not available")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating presigned URL: {e}")

def delete_file_from_spaces(file_path: str):
    s3_client = boto3.client('s3',
                             aws_access_key_id=settings.SPACES_ACCESS_KEY_ID,
                             aws_secret_access_key=settings.SPACES_SECRET_ACCESS_KEY,
                             endpoint_url=settings.SPACES_ENDPOINT)
    try:
        s3_client.delete_object(Bucket='snapgoated', Key=file_path)
        return file_path
    except NoCredentialsError:
        logger.error("Credentials not available")
        raise HTTPException(status_code=500, detail="File deletion failed")
    except Exception as e:
        logger.error(f"Error deleting file: {e}")

