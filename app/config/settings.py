import os
import boto3

class Settings:
    def __init__(self):
        ssm = boto3.client('ssm', region_name="ap-southeast-7")
        self.DATABASE_URL = self.get_parameter(ssm, "DATABASE_URL")
        self.SECRET_KEY = self.get_parameter(ssm, "SECRET_KEY")
        self.SPACES_ACCESS_KEY_ID = self.get_parameter(ssm, "SPACES_ACCESS_KEY_ID")
        self.SPACES_SECRET_ACCESS_KEY = self.get_parameter(ssm, "SPACES_SECRET_ACCESS_KEY")
        self.SPACES_ENDPOINT = self.get_parameter(ssm, "SPACES_ENDPOINT")

        # self.DATABASE_PW = os.getenv("DATABASE_PW")
        # self.DATABASE_PORT = os.getenv("DATABASE_PORT")
        # self.DATABASE_URL = f"postgresql://{self.DATABASE_USER}:{self.DATABASE_PW}@{self.DATABASE_HOST}:{self.DATABASE_PORT}"
        # self.SECRET_KEY = os.getenv("SECRET_KEY")
        # self.DEBUG = True
        # self.ALGORITHM = "HS256"
        # self.ACCESS_TOKEN_EXPIRE_MINUTES = 30
        # self.DLIB_FACE_RECOGNITION_MODEL_FILE = os.getenv("DLIB_FACE_RECOGNITION_MODEL_FILE")
        # self.SHAPE_PREDICTOR_MODEL_FILE = os.getenv("SHAPE_PREDICTOR_MODEL_FILE")
        # self.MODEL_DIR = os.getenv("MODEL_DIR")
        # self.DLIB_FACE_RECOGNITION_MODEL_PATH = f"{self.MODEL_DIR}/{self.DLIB_FACE_RECOGNITION_MODEL_FILE}"
        # self.SHAPE_PREDICTOR_MODEL_PATH = f"{self.MODEL_DIR}/{self.SHAPE_PREDICTOR_MODEL_FILE}"
        # self.AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
        # self.AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
        # self.AWS_REGION = os.getenv("AWS_REGION")
        # self.S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")

    def get_parameter(self, ssm, name):
        return ssm.get_parameter(Name=name, WithDecryption=True)['Parameter']['Value']

settings = Settings()
