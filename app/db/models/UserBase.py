from pydantic import BaseModel
from typing import Optional

class UserBase(BaseModel):
    username: str
    email: str
    full_name: Optional[str] = None
    role: str

    class Config:
        from_attributes: True