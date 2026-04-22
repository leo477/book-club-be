import time
import uuid
from unittest.mock import MagicMock, patch

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from app.config import Settings
from app.services.auth_service import decode_access_token


@pytest.fixture(scope="module")
def rsa_key_pair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


@pytest.fixture(scope="module")
def test_settings():
    return Settings.model_construct(
        SUPABASE_URL="https://test.supabase.co",
        SUPABASE_ANON_KEY="test-anon-key",
    )


def _mock_jwks(public_key, algorithm: str = "RS256"):
    mock_signing_key = MagicMock()
    mock_signing_key.key = public_key
    mock_signing_key.algorithm_name = algorithm
    mock_client = MagicMock()
    mock_client.get_signing_key_from_jwt.return_value = mock_signing_key
    return mock_client


def test_decode_access_token_valid(rsa_key_pair, test_settings):
    private_key, public_key = rsa_key_pair
    user_id = str(uuid.uuid4())
    token = pyjwt.encode(
        {"sub": user_id, "exp": int(time.time()) + 3600},
        private_key,
        algorithm="RS256",
    )

    with patch("app.services.auth_service.PyJWKClient", return_value=_mock_jwks(public_key)):
        payload = decode_access_token(token, test_settings)

    assert payload["sub"] == user_id


def test_decode_access_token_expired(rsa_key_pair, test_settings):
    private_key, public_key = rsa_key_pair
    token = pyjwt.encode(
        {"sub": str(uuid.uuid4()), "exp": int(time.time()) - 10},
        private_key,
        algorithm="RS256",
    )

    with patch("app.services.auth_service.PyJWKClient", return_value=_mock_jwks(public_key)):
        with pytest.raises(HTTPException) as exc_info:
            decode_access_token(token, test_settings)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["code"] == "INVALID_TOKEN"


def test_decode_access_token_invalid_signature(rsa_key_pair, test_settings):
    private_key, public_key = rsa_key_pair
    other_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = pyjwt.encode(
        {"sub": str(uuid.uuid4()), "exp": int(time.time()) + 3600},
        other_private_key,
        algorithm="RS256",
    )

    with patch("app.services.auth_service.PyJWKClient", return_value=_mock_jwks(public_key)):
        with pytest.raises(HTTPException) as exc_info:
            decode_access_token(token, test_settings)

    assert exc_info.value.status_code == 401


HS256_SECRET = "test-supabase-jwt-secret-32characters!!"


@pytest.fixture(scope="module")
def hs256_settings():
    return Settings.model_construct(
        SUPABASE_URL="https://test.supabase.co",
        SUPABASE_ANON_KEY="test-anon-key",
        SUPABASE_JWT_SECRET=HS256_SECRET,
    )


def test_decode_hs256_valid(hs256_settings):
    user_id = str(uuid.uuid4())
    token = pyjwt.encode(
        {"sub": user_id, "exp": int(time.time()) + 3600},
        HS256_SECRET,
        algorithm="HS256",
    )
    payload = decode_access_token(token, hs256_settings)
    assert payload["sub"] == user_id


def test_decode_hs256_expired(hs256_settings):
    token = pyjwt.encode(
        {"sub": str(uuid.uuid4()), "exp": int(time.time()) - 10},
        HS256_SECRET,
        algorithm="HS256",
    )
    with pytest.raises(HTTPException) as exc_info:
        decode_access_token(token, hs256_settings)
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["code"] == "INVALID_TOKEN"


def test_decode_hs256_wrong_secret(hs256_settings):
    token = pyjwt.encode(
        {"sub": str(uuid.uuid4()), "exp": int(time.time()) + 3600},
        "wrong-secret-that-does-not-match",
        algorithm="HS256",
    )
    with pytest.raises(HTTPException) as exc_info:
        decode_access_token(token, hs256_settings)
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["code"] == "INVALID_TOKEN"
