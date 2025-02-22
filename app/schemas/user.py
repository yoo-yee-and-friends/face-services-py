from pydantic import BaseModel, EmailStr
from typing import Optional

class UserCreate(BaseModel):
    username: str
    password: str
    agree_policy: bool
    display_name: str
    email: str
    otp_code: str

class SignupResponse(BaseModel):
    message: str
    user: str

class UserProfile(BaseModel):
    display_name: str
    email: str
    profile_picture: Optional[str] = None

    class Config:
        from_attributes = True

class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    username: Optional[str] = None

class CheckUserExistenceInput(BaseModel):
    display_name: str
    username: str
    email: EmailStr
    is_agree_policy: bool

class Response(BaseModel):
    message: str
    status: str
    status_code: int
    data: Optional[dict] = None

class SendVerificationCodeInput(BaseModel):
    email: EmailStr