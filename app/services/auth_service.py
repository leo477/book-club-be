from typing import Any

import jwt
import structlog
from fastapi import HTTPException, status
from jwt import PyJWKClient
from jwt.exceptions import PyJWTError
from supabase import AsyncClient, acreate_client
from supabase_auth.errors import AuthApiError
from supabase_auth.types import AuthResponse

from app.config import Settings

logger = structlog.get_logger()


async def get_supabase_client(settings: Settings) -> AsyncClient:
    return await acreate_client(settings.SUPABASE_URL, settings.SUPABASE_ANON_KEY)


async def supabase_sign_up(
    client: AsyncClient,
    email: str,
    password: str,
    display_name: str,
    role: str,
) -> AuthResponse:
    try:
        return await client.auth.sign_up(
            {
                "email": email,
                "password": password,
                "options": {"data": {"display_name": display_name, "role": role}},
            }
        )
    except AuthApiError as exc:
        msg = str(exc).lower()
        if "already registered" in msg or "already been registered" in msg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"error": "Email already exists", "code": "EMAIL_EXISTS"},
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": str(exc), "code": "SUPABASE_AUTH_ERROR"},
        ) from exc


async def supabase_sign_in(client: AsyncClient, email: str, password: str) -> AuthResponse:
    try:
        return await client.auth.sign_in_with_password({"email": email, "password": password})
    except AuthApiError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "Invalid credentials", "code": "INVALID_CREDENTIALS"},
        ) from exc


def decode_access_token(token: str, settings: Settings) -> dict[str, Any]:
    try:
        jwks_client = PyJWKClient(f"{settings.SUPABASE_URL}/auth/v1/.well-known/jwks.json")
        signing_key = jwks_client.get_signing_key_from_jwt(token)
        payload: dict[str, Any] = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            options={"verify_aud": False},
        )
        return payload
    except PyJWTError as exc:
        logger.warning("JWT decode failed", error=str(exc), exc_type=type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "Invalid or expired token", "code": "INVALID_TOKEN"},
        ) from exc
