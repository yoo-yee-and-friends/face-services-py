# Import all tasks to ensure they're registered with Celery
from app.core.celery_app import celery_app
import app.tasks.face_detection