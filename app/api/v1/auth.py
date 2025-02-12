import re
from datetime import timedelta, datetime
from jose import JWTError, jwt
from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.db.models.VerificationCode import VerificationCode
from app.schemas.user import UserCreate, Token, TokenData, CheckUserExistenceInput, SendVerificationCodeInput, \
    UserProfile
from app.security.auth import authenticate_user, create_access_token, get_password_hash, ACCESS_TOKEN_EXPIRE_MINUTES, \
    SECRET_KEY, ALGORITHM, get_current_active_user

from app.db.models.User import User
from app.db.session import get_db
from app.crud.user import get_user
from app.utils.email_utils import send_verification_email
from app.utils.validation import validate_user_input, generate_verification_code


pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")
EMAIL_REGEX = r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$'

router = APIRouter()

@router.post("/signup")
async def signup(user: UserCreate, db: Session = Depends(get_db)):
    errors = validate_user_input(user)
    if errors:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=errors
        )

    existing_user = get_user(db, user.username)
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already registered " + user.username
        )

    verification = db.query(VerificationCode).filter(
        VerificationCode.email == user.email,
        VerificationCode.code == user.otp_code,
        VerificationCode.purpose == "register_code",
        VerificationCode.expired_at > datetime.utcnow()
    ).first()

    if not verification:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired OTP code"
        )

    new_user = User(
        username=user.username,
        password_hash=get_password_hash(user.password),
        role_id=1,
        display_name=user.display_name,
        email=user.email,
        agree_policy=user.agree_policy,
        email_verified=True
    )
    try:
        db.add(new_user)
        db.commit()
        db.refresh(new_user)
        return {"message": "User created successfully", "user": new_user.username}
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred: {str(e)}"
        )

@router.post("/validate_register_form")
async def validate_register_form(input: CheckUserExistenceInput, db: Session = Depends(get_db)):
    if input.display_name:
        user = db.query(User).filter(User.display_name == input.display_name).first()
        if user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Display name already exists"
            )

    if input.username:
        user = db.query(User).filter(User.username == input.username).first()
        if user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Username already exists"
            )

    if input.email:
        email_regex = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')
        if not email_regex.match(input.email):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid email format"
            )

        user = db.query(User).filter(User.email == input.email).first()
        if user:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Email already exists"
            )

    return {"status": "success", "message": "No conflicts found"}

@router.post("/send_verification_code")
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
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Please wait 3 minutes before requesting a new verification code"
            )

        # Update the existing verification code
        existing_verification.code = verification_code
        existing_verification.expired_at = datetime.utcnow() + timedelta(minutes=5)
        existing_verification.updated_at = datetime.utcnow()
        try:
            db.commit()
        except Exception as e:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update verification code"
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
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create verification code"
            )

    # Send verification code to user's email
    ip_address = request.client.host
    device = request.headers.get('User-Agent')
    if not send_verification_email(input.email, verification_code, ip_address, device):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send verification email"
        )

    return {"status": "success", "message": "Verification code sent successfully. Please check your email."}


@router.post("/login", response_model=Token)
async def login_for_access_token(
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

    return {"access_token": access_token, "token_type": "bearer"}

@router.get("/user/profile", response_model=UserProfile)
async def get_user_profile(current_user: User = Depends(get_current_active_user)):
    return UserProfile(
        display_name=current_user.display_name,
        email=current_user.email,
        profile_picture=current_user.profile_photo  # Adjust this field based on your User model
    )

async def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
        token_data = TokenData(username=username)
    except JWTError:
        raise credentials_exception
    user = get_user(db, username=token_data.username)
    if user is None:
        raise credentials_exception
    return user

def get_current_active_user(current_user: User = Depends(get_current_user)):
    if current_user.role.name not in ["general_user"]:
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user

def check_staff_user(current_user: User = Depends(get_current_user)):
    if current_user.role.name in ["guest", "general_user"]:
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user
