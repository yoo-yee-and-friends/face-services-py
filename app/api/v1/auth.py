import re
from datetime import timedelta, datetime

from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordRequestForm
from passlib.context import CryptContext
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from app.db.models.VerificationCode import VerificationCode
from app.schemas.user import UserCreate, Token, CheckUserExistenceInput, SendVerificationCodeInput, Response
from app.security.auth import authenticate_user, create_access_token, get_password_hash, ACCESS_TOKEN_EXPIRE_MINUTES, get_current_active_user

from app.db.models.User import User
from app.db.session import get_db
from app.crud.user import get_user
from app.utils.email_utils import send_verification_email
from app.utils.validation import validate_user_input, generate_verification_code


pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
EMAIL_REGEX = r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$'
limiter = Limiter(key_func=get_remote_address)
router = APIRouter()

# @router.post("/signup", response_model=Response)
async def signup(user: UserCreate, db: Session = Depends(get_db)):
    errors = validate_user_input(user)
    if errors:
        return Response(
            status="error",
            message="Validation failed",
            status_code=status.HTTP_400_BAD_REQUEST,
            data={"errors": errors}
        )

    if len(user.username) < 8:
        return Response(
            status="error",
            message="Username must be at least 8 characters long",
            status_code=status.HTTP_400_BAD_REQUEST
        )

    existing_user = get_user(db, user.username)
    if existing_user:
        return Response(
            status="error",
            message="Username already exists",
            status_code=status.HTTP_400_BAD_REQUEST
        )

    verification = db.query(VerificationCode).filter(
        VerificationCode.email == user.email,
        VerificationCode.code == user.otp_code,
        VerificationCode.purpose == "register_code",
        VerificationCode.expired_at > datetime.utcnow()
    ).first()

    if not verification:
        return Response(
            status="error",
            message="Invalid verification code",
            status_code=status.HTTP_400_BAD_REQUEST
        )

    new_user = User(
        username=user.username,
        password_hash=get_password_hash(user.password),
        role_id = 2,
        display_name=user.display_name,
        email=user.email,
        agree_policy=user.agree_policy,
        created_at=datetime.utcnow(),
        email_verified=True
    )
    try:
        db.add(new_user)
        db.commit()
        db.refresh(new_user)

        access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        access_token = create_access_token(
            data={"sub": new_user.username, "userId": new_user.id},
            expires_delta=access_token_expires
        )

        return Response(
            status="success",
            message="User created successfully",
            status_code=status.HTTP_201_CREATED,
            data={"access_token": access_token}
        )
    except Exception as e:
        db.rollback()
        return Response(
            status="error",
            message="Failed to create user " + str(e) ,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

@router.post("/validate-register-form", response_model=Response)
async def validate_register_form(
        input: CheckUserExistenceInput,
        db: Session = Depends(get_db)
):
    if input.display_name:
        user = db.query(User).filter(User.display_name == input.display_name).first()
        if user:
            return Response(
                status="error",
                message="Display name already exists",
                status_code=status.HTTP_400_BAD_REQUEST
            )

    if input.username:
        user = db.query(User).filter(User.username == input.username).first()
        if user:
            return Response(
                status="error",
                message="Username already exists",
                status_code=status.HTTP_400_BAD_REQUEST
            )

    if input.email:
        email_regex = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')
        if not email_regex.match(input.email):
            return Response(
                status="error",
                message="Invalid email format",
                status_code=status.HTTP_400_BAD_REQUEST
            )

    user = db.query(User).filter(User.email == input.email).first()
    if user:
        return Response(
            status="error",
            message="Email already exists",
            status_code=status.HTTP_400_BAD_REQUEST
        )

    if not input.is_agree_policy:
        return Response(
            status="error",
            message="Please agree to the privacy policy",
            status_code=status.HTTP_400_BAD_REQUEST
        )

    # Check and delete the email in the VerificationCode table
    verification_code = db.query(VerificationCode).filter(VerificationCode.email == input.email).first()
    if verification_code:
        db.delete(verification_code)
        db.commit()

    return Response(
        status="success",
        message="Validation successful",
        status_code=status.HTTP_200_OK
    )

@router.post("/send-verification-code", response_model=Response)
@limiter.limit("3/5minutes")
async def send_verification_code(input: SendVerificationCodeInput, request: Request, db: Session = Depends(get_db)):
    verification_code = generate_verification_code()

    # Check if a verification code already exists for this email
    existing_verification = db.query(VerificationCode).filter(
        VerificationCode.email == input.email,
        VerificationCode.purpose == "register_code"
    ).first()

    if existing_verification:
        # Check if the last request was made within the last 3 minutes
        time_since_last_request = datetime.utcnow() - existing_verification.updated_at
        if time_since_last_request < timedelta(minutes=3):
            return  Response(
                status="error",
                message="Please wait for 3 minutes before requesting a new verification code",
                status_code=status.HTTP_400_BAD_REQUEST
            )

        # Update the existing verification code
        existing_verification.code = verification_code
        existing_verification.expired_at = datetime.utcnow() + timedelta(minutes=5)
        existing_verification.updated_at = datetime.utcnow()
        try:
            db.commit()
        except Exception as e:
            db.rollback()
            return Response(
                status="error",
                message="Failed to update verification code",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    else:
        # Create a new verification code
        new_verification = VerificationCode(
            email=input.email,
            code=verification_code,
            purpose="register_code",
            expired_at=datetime.utcnow() + timedelta(minutes=5),
            updated_at=datetime.utcnow()
        )
        try:
            db.add(new_verification)
            db.commit()
        except Exception as e:
            db.rollback()
            return Response(
                status="error",
                message="Failed to create verification code",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    # Send verification code to user's email
    ip_address = request.client.host
    device = request.headers.get('User-Agent')
    if not send_verification_email(input.email, verification_code, ip_address, device):
        return Response(
            status="error",
            message="Failed to send verification code",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

    return Response(
        status="success",
        message="Verification code sent successfully. Please check your email.",
        status_code=status.HTTP_200_OK
    )

@router.post("/login", response_model=Response)
async def login_for_access_token(
        form_data: OAuth2PasswordRequestForm = Depends(),
        db: Session = Depends(get_db)
) -> Response:
    user = authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username, "userId": user.id},
        expires_delta=access_token_expires
    )

    return Response(
        status="success",
        message="Login successful",
        status_code=status.HTTP_200_OK,
        data={"access_token": access_token, "token_type": "bearer"}
    )

@router.post("/login-test", response_model=Token)
async def login_for_test(
        form_data: OAuth2PasswordRequestForm = Depends(),
        db: Session = Depends(get_db)
) -> Token:
    user = authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username, "userId": user.id},
        expires_delta=access_token_expires
    )

    return Token(access_token=access_token, token_type="bearer")

@router.get("/user/profile", response_model=Response)
async def get_user_profile(current_user: User = Depends(get_current_active_user)):
    return Response(
        status="success",
        message="User profile retrieved successfully",
        status_code=status.HTTP_200_OK,
        data={
            "display_name": current_user.display_name,
            "email": current_user.email,
            "profile_picture": current_user.profile_photo
        }
    )

