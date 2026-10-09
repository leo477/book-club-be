import time
import uuid
from unittest.mock import MagicMock, patch

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from fastapi import HTTPException

from app.config import Settings
from app.services import auth_service
from app.services.auth_service import decode_access_token


@pytest.fixture(autouse=True)
def _clear_jwks_cache():
    auth_service._jwks_clients.clear()
    yield
    auth_service._jwks_clients.clear()


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


@pytest.fixture(scope="module")
def ec_key():
    return ec.generate_private_key(ec.SECP256R1())


def _claims(delta: int = 3600) -> dict:
    return {"sub": str(uuid.uuid4()), "exp": int(time.time()) + delta}


def test_decode_es256_valid_with_secret_configured(ec_key, hs256_settings):
    claims = _claims()
    token = pyjwt.encode(claims, ec_key, algorithm="ES256", headers={"kid": "k1"})
    with patch("app.services.auth_service.PyJWKClient", return_value=_mock_jwks(ec_key.public_key(), "ES256")):
        payload = decode_access_token(token, hs256_settings)
    assert payload["sub"] == claims["sub"]


def test_decode_es256_expired(ec_key, test_settings):
    token = pyjwt.encode(_claims(-10), ec_key, algorithm="ES256")
    with patch("app.services.auth_service.PyJWKClient", return_value=_mock_jwks(ec_key.public_key(), "ES256")):
        with pytest.raises(HTTPException) as exc_info:
            decode_access_token(token, test_settings)
    assert exc_info.value.detail["code"] == "INVALID_TOKEN"


def test_decode_es256_tampered(ec_key, test_settings):
    token = pyjwt.encode(_claims(), ec_key, algorithm="ES256")
    head, body, sig = token.split(".")
    forged = pyjwt.encode(_claims(), ec_key, algorithm="ES256").split(".")[1]
    with patch("app.services.auth_service.PyJWKClient", return_value=_mock_jwks(ec_key.public_key(), "ES256")):
        with pytest.raises(HTTPException) as exc_info:
            decode_access_token(f"{head}.{forged}.{sig}", test_settings)
    assert exc_info.value.status_code == 401


def test_decode_es256_jwks_fetch_error(ec_key, test_settings):
    token = pyjwt.encode(_claims(), ec_key, algorithm="ES256")
    client = MagicMock()
    client.get_signing_key_from_jwt.side_effect = pyjwt.PyJWKClientConnectionError("down")
    with patch("app.services.auth_service.PyJWKClient", return_value=client):
        with pytest.raises(HTTPException) as exc_info:
            decode_access_token(token, test_settings)
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["code"] == "INVALID_TOKEN"


def test_decode_jwks_client_is_cached(ec_key, test_settings):
    token = pyjwt.encode(_claims(), ec_key, algorithm="ES256")
    with patch("app.services.auth_service.PyJWKClient", return_value=_mock_jwks(ec_key.public_key(), "ES256")) as ctor:
        decode_access_token(token, test_settings)
        decode_access_token(token, test_settings)
    assert len(auth_service._jwks_clients) == 1
    assert ctor.call_count >= 1


def test_decode_hs256_without_secret_rejected(test_settings):
    token = pyjwt.encode(_claims(), HS256_SECRET, algorithm="HS256")
    with pytest.raises(HTTPException) as exc_info:
        decode_access_token(token, test_settings)
    assert exc_info.value.detail["code"] == "INVALID_TOKEN"


def test_decode_unsupported_alg_rejected(hs256_settings):
    token = pyjwt.encode(_claims(), HS256_SECRET, algorithm="HS512")
    with pytest.raises(HTTPException) as exc_info:
        decode_access_token(token, hs256_settings)
    assert exc_info.value.detail["code"] == "INVALID_TOKEN"


def test_decode_alg_none_rejected(hs256_settings):
    token = pyjwt.encode(_claims(), None, algorithm="none")
    with pytest.raises(HTTPException) as exc_info:
        decode_access_token(token, hs256_settings)
    assert exc_info.value.detail["code"] == "INVALID_TOKEN"


def test_decode_garbage_rejected(hs256_settings):
    with pytest.raises(HTTPException) as exc_info:
        decode_access_token("not-a-jwt", hs256_settings)
    assert exc_info.value.status_code == 401
