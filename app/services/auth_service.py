from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
from fastapi import HTTPException, status
from jose import JWTError, jwt

from app.config import Settings


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def create_access_token(data: dict[str, Any], settings: Settings) -> str:
    payload: dict[str, Any] = data.copy()
    expire = datetime.now(UTC) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload.update({"exp": expire})
    return str(jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM))


def decode_access_token(token: str, settings: Settings) -> dict[str, Any]:
    try:
        payload: dict[str, Any] = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        return payload
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "Invalid or expired token", "code": "INVALID_TOKEN"},
        ) from exc
