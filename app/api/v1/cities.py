# app/api/v1/cities.py
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.db.models.City import City as CityModel
from app.schemas.city import City
from typing import List

router = APIRouter()
logger = logging.getLogger(__name__)

@router.get("/countries/{country_id}/cities", response_model=List[City])
def get_cities_by_country(country_id: int, db: Session = Depends(get_db)):
    try:
        cities = db.query(CityModel).filter(CityModel.country_id == country_id).all()
        if not cities:
            raise HTTPException(status_code=404, detail="No cities found for the given country ID")
        return cities
    except Exception as e:
        logger.error(f"Error fetching cities for country_id {country_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")