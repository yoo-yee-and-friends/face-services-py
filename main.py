from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.api.v1.auth import router as auth_router
from app.api.v1.events import router as events_router
from app.api.v1.cities import router as cities_router
from app.api.v1.client import public_router

tags_metadata = [
    {
        "name": "auth",
        "description": "Operations related to authentication.",
    },
]

app = FastAPI(
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_tags=tags_metadata,
)

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS policy
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# เพิ่ม API routes
app.include_router(events_router, prefix="/api/v1", tags=["events"])
app.include_router(auth_router, prefix="/api/v1/auth", tags=["auth"])
app.include_router(cities_router, prefix="/api/v1", tags=["cities"])
app.include_router(public_router, prefix="/api/v1", tags=["public"])


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
