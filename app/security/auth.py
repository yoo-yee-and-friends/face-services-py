import logging
import re
from datetime import timedelta, datetime

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session
from jose import JWTError, jwt
from passlib.context import CryptContext
from typing import Optional

from starlette import status

from fastapi.security import OAuth2PasswordBearer
from starlette.websockets import WebSocket

from app.config.settings import settings
from app.crud.user import get_user
from app.db.models.User import User
from app.db.session import get_db
from app.schemas.user import TokenData

SECRET_KEY = settings.SECRET_KEY
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 1440
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/v1/auth/login-test")

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def authenticate_user(db: Session, username_or_email: str, password: str):
    user = db.query(User).filter(
        (User.username == username_or_email) | (User.email == username_or_email)
    ).first()
    if not user:
        return False
    if not pwd_context.verify(password, user.password_hash):
        return False
    if not user.email_verified:
        return False
    return user

def validate_password(password: str) -> bool:
    has_min_len = len(password) >= 8
    has_upper = re.search(r'[A-Z]', password) is not None
    has_lower = re.search(r'[a-z]', password) is not None
    has_number = re.search(r'[0-9]', password) is not None
    has_special = re.search(r'[!@#\$%\^&\*\(\)_\+]', password) is not None
    return has_min_len and has_upper and has_lower and has_number and has_special

async def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        logging.info(f"username: {username}")
        if username is None:
            raise credentials_exception
        token_data = TokenData(username=username)
    except JWTError:
        raise credentials_exception
    user = get_user(db, username=token_data.username)
    if user is None:
        raise credentials_exception
    return user


async def get_ws_current_user(
        websocket: WebSocket,
        db: Session = Depends(get_db)
):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        # Extract token from the WebSocket request header
        authorization_header = websocket.headers.get("Authorization")
        if authorization_header is None:
            raise credentials_exception

        token = authorization_header.split(" ")[1]  # Get the token from 'Bearer <token>'

        # Decode the token
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        logging.info(f"username: {username}")
        if username is None:
            raise credentials_exception

        # Create TokenData object
        token_data = TokenData(username=username)
    except JWTError:
        raise credentials_exception

    # Get user from database
    user = get_user(db, username=token_data.username)
    if user is None:
        raise credentials_exception
    return user

def get_current_active_user(current_user: User = Depends(get_current_user)):
    if current_user.role.id not in [2]:
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user

def get_ws_current_active_user(current_user: User = Depends(get_ws_current_user)):
    if current_user.role.id not in [2]:
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user