# app/utils/validation.py
import random
import re
import time
from datetime import datetime

from fastapi import HTTPException

from app.security.auth import validate_password

EMAIL_REGEX = r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$'

def validate_user_input(user):
    errors = []
    if not user.agree_policy:
        errors.append("You must agree to the privacy policy")
    if not validate_password(user.password):
        errors.append("Password must be at least 8 characters long and contain at least one uppercase letter, "
                      "one lowercase letter, one number, and one special character")
    if not re.match(EMAIL_REGEX, user.email):
        errors.append("Invalid email format")
    return errors

def generate_verification_code():
    random.seed(time.time())
    code = random.randint(0, 999999)
    return f"{code:06d}"

def validate_password(password: str) -> bool:
    has_min_len = len(password) >= 8
    has_upper = re.search(r'[A-Z]', password) is not None
    has_lower = re.search(r'[a-z]', password) is not None
    has_number = re.search(r'[0-9]', password) is not None
    has_special = re.search(r'[!@#\$%\^&\*\(\)_\+]', password) is not None

    return has_min_len and has_upper and has_lower and has_number and has_special

def validate_date_format(date_str: str):
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return True
    except ValueError:
        return False

def format_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.2f} KB"
    elif size < 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.2f} MB"
    else:
        return f"{size / (1024 * 1024 * 1024):.2f} GB"
