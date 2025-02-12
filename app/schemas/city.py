# app/schemas/city.py
from pydantic import BaseModel

class City(BaseModel):
    id: int
    name_en: str
    name_th: str

    class Config:
        orm_mode: True