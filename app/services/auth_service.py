from typing import Any, cast

import jwt
import structlog
from fastapi import HTTPException, status
from jwt import PyJWKClient
from jwt.exceptions import PyJWTError
from jwt.types import Options
from supabase import AsyncClient, acreate_client
from supabase_auth.errors import AuthApiError
from supabase_auth.types import AuthResponse, CodeExchangeParams, Provider

from app.config import Settings

logger = structlog.get_logger()

_jwks_clients: dict[str, PyJWKClient] = {}
# M-9: cache async Supabase client by URL to avoid re-creating on every request
_supabase_clients: dict[str, AsyncClient] = {}


async def get_supabase_client(settings: Settings) -> AsyncClient:
    key = settings.SUPABASE_URL
    if key not in _supabase_clients:
        _supabase_clients[key] = await acreate_client(settings.SUPABASE_URL, settings.SUPABASE_ANON_KEY)
    return _supabase_clients[key]


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


async def supabase_refresh(client: AsyncClient, refresh_token: str) -> AuthResponse:
    try:
        return await client.auth.refresh_session(refresh_token)
    except AuthApiError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "Invalid or expired refresh token", "code": "INVALID_REFRESH_TOKEN"},
        ) from exc


async def supabase_sign_in(client: AsyncClient, email: str, password: str) -> AuthResponse:
    try:
        return await client.auth.sign_in_with_password({"email": email, "password": password})
    except AuthApiError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "Invalid credentials", "code": "INVALID_CREDENTIALS"},
        ) from exc


async def supabase_oauth_url(client: AsyncClient, provider: Provider, redirect_to: str) -> str:
    """Return the provider authorize URL the browser should be redirected to.

    The PKCE code_verifier generated here is stored on the cached AsyncClient and
    consumed by supabase_exchange_code on the callback request.
    """
    try:
        resp = await client.auth.sign_in_with_oauth({"provider": provider, "options": {"redirect_to": redirect_to}})
    except AuthApiError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": str(exc), "code": "OAUTH_INIT_ERROR"},
        ) from exc
    if not resp.url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "OAuth provider unavailable", "code": "OAUTH_INIT_ERROR"},
        )
    return resp.url


async def supabase_exchange_code(client: AsyncClient, code: str) -> AuthResponse:
    # code_verifier/redirect_to are required by the TypedDict but are recovered
    # from the client's PKCE storage at runtime, so only auth_code is supplied.
    params = cast(CodeExchangeParams, {"auth_code": code})
    try:
        return await client.auth.exchange_code_for_session(params)
    except AuthApiError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "Invalid or expired authorization code", "code": "OAUTH_EXCHANGE_ERROR"},
        ) from exc


_ASYMMETRIC_ALGS = frozenset({"ES256", "RS256"})
_JWT_DECODE_FAILED = "JWT decode failed"
# Supabase access tokens carry aud="authenticated" (or omit aud); we don't gate on it.
_DECODE_OPTIONS: Options = {"verify_aud": False}


def _invalid_token() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"error": "Invalid or expired token", "code": "INVALID_TOKEN"},
    )


def decode_access_token(token: str, settings: Settings) -> dict[str, Any]:
    try:
        # Header alg only selects the pinned algorithm; jwt.decode verifies the signature.
        alg = jwt.get_unverified_header(token).get("alg")  # NOSONAR
        if alg == "HS256":
            if not settings.SUPABASE_JWT_SECRET:
                logger.warning(_JWT_DECODE_FAILED, error="HS256 token but no JWT secret configured")
                raise _invalid_token()
            payload: dict[str, Any] = jwt.decode(
                token, settings.SUPABASE_JWT_SECRET, algorithms=["HS256"], options=_DECODE_OPTIONS
            )
        elif alg in _ASYMMETRIC_ALGS:
            if not settings.SUPABASE_URL:
                logger.warning(_JWT_DECODE_FAILED, error="SUPABASE_URL not configured")
                raise _invalid_token()
            jwks_client = _jwks_clients.get(settings.SUPABASE_URL)
            if jwks_client is None:
                jwks_url = f"{settings.SUPABASE_URL}/auth/v1/.well-known/jwks.json"
                jwks_client = _jwks_clients[settings.SUPABASE_URL] = PyJWKClient(jwks_url, timeout=5)
            signing_key = jwks_client.get_signing_key_from_jwt(token)
            payload = jwt.decode(token, signing_key.key, algorithms=[alg], options=_DECODE_OPTIONS)
        else:
            logger.warning(_JWT_DECODE_FAILED, error="unsupported alg", alg=alg)
            raise _invalid_token()
        return payload
    except (PyJWTError, TypeError) as exc:
        logger.warning(_JWT_DECODE_FAILED, error=str(exc), exc_type=type(exc).__name__)
        raise _invalid_token() from exc
