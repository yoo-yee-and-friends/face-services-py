import dropbox
from app.config.settings import settings
from fastapi import UploadFile
import os

def upload_to_dropbox(file: UploadFile, destination_path: str) -> tuple:
    dbx = dropbox.Dropbox(settings.DROPBOX_ACCESS_TOKEN)

    if not destination_path.startswith('/'):
        destination_path = '/' + destination_path

    file_content = file.file.read()
    dbx.files_upload(file_content, destination_path)

    shared_link_metadata = dbx.sharing_create_shared_link_with_settings(destination_path)

    preview_url = shared_link_metadata.url.replace("dl=0", "raw=1")
    download_url = shared_link_metadata.url.replace("dl=0", "dl=1")
    return preview_url, download_url


def download_file_from_dropbox(file_path, dropbox_path):
    dbx = dropbox.Dropbox(settings.DROPBOX_ACCESS_TOKEN)
    if not dbx:
        raise Exception("Error connecting to Dropbox")
    try:
        with open(file_path, "wb") as f:
            metadata, res = dbx.files_download(dropbox_path)
            f.write(res.content)
    except dropbox.exceptions.ApiError as err:
        raise Exception(f"Failed to download {dropbox_path} from Dropbox: {err}")


def check_and_download_models():
    print("Checking and downloading models...")
    if not os.path.exists(settings.DLIB_FACE_RECOGNITION_MODEL_PATH):
        print(f"{settings.DLIB_FACE_RECOGNITION_MODEL_FILE} not found. Downloading from Dropbox...")
        download_file_from_dropbox(settings.DLIB_FACE_RECOGNITION_MODEL_PATH, f"/{settings.DLIB_FACE_RECOGNITION_MODEL_FILE}")

    if not os.path.exists(settings.SHAPE_PREDICTOR_MODEL_PATH):
        print(f"{settings.SHAPE_PREDICTOR_MODEL_FILE} not found. Downloading from Dropbox...")
        download_file_from_dropbox(settings.SHAPE_PREDICTOR_MODEL_PATH, f"/{settings.SHAPE_PREDICTOR_MODEL_FILE}")