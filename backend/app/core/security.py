from datetime import datetime, timedelta, timezone

from jose import jwt, JWTError
from passlib.context import CryptContext
import pyotp

from app.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ─── Password ───────────────────────────────────────────

def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


# ─── JWT ────────────────────────────────────────────────

def create_access_token(data: dict, expire_minutes: int | None = None) -> str:
    to_encode = data.copy()
    minutes = expire_minutes or settings.ACCESS_TOKEN_EXPIRE_MINUTES
    expire = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(data: dict, expire_days: int | None = None) -> str:
    to_encode = data.copy()
    days = expire_days or settings.REFRESH_TOKEN_EXPIRE_DAYS
    expire = datetime.now(timezone.utc) + timedelta(days=days)
    to_encode.update({"exp": expire, "type": "refresh"})
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> dict | None:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        return payload
    except JWTError:
        return None


# ─── TOTP (2FA) ─────────────────────────────────────────

def generate_totp_secret() -> str:
    return pyotp.random_base32()


def get_totp_uri(secret: str, username: str) -> str:
    return pyotp.totp.TOTP(secret).provisioning_uri(
        name=username,
        issuer_name=settings.APP_NAME,
    )


def verify_totp(secret: str, code: str) -> bool:
    totp = pyotp.TOTP(secret)
    return totp.verify(code, valid_window=1)
