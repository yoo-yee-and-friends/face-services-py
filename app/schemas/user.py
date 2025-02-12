from pydantic import BaseModel, EmailStr
from typing import Optional

class UserCreate(BaseModel):
    username: str
    password: str
    agree_policy: bool
    display_name: str
    email: str
    role_id: int
    otp_code: str

class UserProfile(BaseModel):
    display_name: str
    email: str
    profile_picture: Optional[str] = None

    class Config:
        orm_mode = True

class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    username: Optional[str] = None

class CheckUserExistenceInput(BaseModel):
    display_name: str
    username: str
    email: EmailStr

class SendVerificationCodeInput(BaseModel):
    email: EmailStr