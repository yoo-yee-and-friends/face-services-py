import boto3
from botocore.exceptions import NoCredentialsError, ClientError
from fastapi import HTTPException
from app.config.settings import settings
from fastapi import UploadFile
import os

# s3_client = boto3.client('s3', aws_access_key_id=settings.AWS_ACCESS_KEY_ID, aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY, region_name=settings.AWS_REGION)

# def upload_to_s3(file: UploadFile, object_name: str = None) -> tuple:
#     if object_name is None:
#         object_name = file.filename
#     try:
#         file.file.seek(0)
#         s3_client.upload_fileobj(
#             file.file,
#             settings.S3_BUCKET_NAME,
#             object_name,
#             ExtraArgs={'ContentType': file.content_type}
#         )
#         return True
#     except NoCredentialsError:
#         raise HTTPException(status_code=500, detail="Credentials not available")
#     except Exception as e:
#         raise HTTPException(status_code=500, detail=str(e))
#
#
# def download_file_from_s3(file_path, bucket_name, object_name):
#     try:
#         os.makedirs(os.path.dirname(file_path), exist_ok=True)
#         with open(file_path, "wb") as f:
#            s3_client.download_fileobj(bucket_name, object_name, f)
#     except NoCredentialsError:
#         raise HTTPException(status_code=500, detail="Credentials not available")
#     except ClientError as e:
#         if e.response['Error']['Code'] == '404':
#             raise HTTPException(status_code=404, detail="Object not found in S3 bucket")
#         else:
#             raise HTTPException(status_code=500, detail=str(e))
#     except Exception as e:
#         raise HTTPException(status_code=500, detail=str(e))
#
#
# def check_and_download_models():
#     print("Checking and downloading models...")
#     if not os.path.exists(settings.DLIB_FACE_RECOGNITION_MODEL_PATH):
#         print(f"{settings.DLIB_FACE_RECOGNITION_MODEL_FILE} not found. Downloading from s3...")
#         download_file_from_s3(settings.DLIB_FACE_RECOGNITION_MODEL_PATH, settings.S3_BUCKET_NAME, settings.DLIB_FACE_RECOGNITION_MODEL_PATH)
#
#     if not os.path.exists(settings.SHAPE_PREDICTOR_MODEL_PATH):
#         print(f"{settings.SHAPE_PREDICTOR_MODEL_FILE} not found. Downloading from s3...")
#         download_file_from_s3(settings.SHAPE_PREDICTOR_MODEL_PATH, settings.S3_BUCKET_NAME, settings.SHAPE_PREDICTOR_MODEL_PATH)
#
# def generate_presigned_url(object_name):
#     try:
#         presigned_url = s3_client.generate_presigned_url('get_object', Params={'Bucket': settings.S3_BUCKET_NAME, 'Key': object_name}, ExpiresIn=86400)
#         return presigned_url
#     except Exception as e:
#         raise HTTPException(status_code=500, detail=str(e))