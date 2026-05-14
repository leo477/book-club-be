import io
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_supabase(public_url: str = "https://cdn.example.com/covers/test.jpg") -> MagicMock:
    mock_supabase = MagicMock()
    mock_supabase.storage.from_.return_value.upload.return_value = None
    mock_supabase.storage.from_.return_value.get_public_url.return_value = public_url
    return mock_supabase


def _make_mock_settings() -> MagicMock:
    """Return a settings mock that looks configured (non-empty Supabase credentials)."""
    s = MagicMock()
    s.SUPABASE_URL = "https://test.supabase.co"
    s.SUPABASE_ANON_KEY = "test-anon-key"
    return s


@contextmanager
def _supabase_patches(mock_supabase: MagicMock):
    """Patch both get_settings (so 503 branch is skipped) and supabase.create_client."""
    with (
        patch("app.routers.upload.get_settings", return_value=_make_mock_settings()),
        patch("supabase.create_client", return_value=mock_supabase),
    ):
        yield


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_cover_unauthenticated(async_client):
    """No auth token → 401."""
    resp = await async_client.post(
        "/api/v1/upload/cover",
        files={"file": ("photo.jpg", io.BytesIO(b"fake-image-data"), "image/jpeg")},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_upload_cover_unsupported_type(async_client, register_user, auth_headers):
    """Uploading a PDF → 415 Unsupported Media Type (checked before settings)."""
    await register_user(email="upload_unsupported@example.com")
    headers = await auth_headers(email="upload_unsupported@example.com")

    resp = await async_client.post(
        "/api/v1/upload/cover",
        headers=headers,
        files={"file": ("document.pdf", io.BytesIO(b"%PDF-1.4 fake"), "application/pdf")},
    )
    assert resp.status_code == 415
    detail = resp.json()["detail"]
    assert detail["code"] == "UNSUPPORTED_MEDIA_TYPE"


@pytest.mark.asyncio
async def test_upload_cover_file_too_large(async_client, register_user, auth_headers):
    """Uploading a file larger than 5 MB → 413."""
    await register_user(email="upload_toolarge@example.com")
    headers = await auth_headers(email="upload_toolarge@example.com")

    large_content = b"x" * (5 * 1024 * 1024 + 1)  # 5 MB + 1 byte
    mock_supabase = _make_mock_supabase()

    with _supabase_patches(mock_supabase):
        resp = await async_client.post(
            "/api/v1/upload/cover",
            headers=headers,
            files={"file": ("big.jpg", io.BytesIO(large_content), "image/jpeg")},
        )

    assert resp.status_code == 413
    detail = resp.json()["detail"]
    assert detail["code"] == "FILE_TOO_LARGE"


@pytest.mark.asyncio
async def test_upload_cover_success(async_client, register_user, auth_headers):
    """Valid image upload → 200 with a url key."""
    await register_user(email="upload_success@example.com")
    headers = await auth_headers(email="upload_success@example.com")

    expected_url = "https://cdn.example.com/covers/test.jpg"
    mock_supabase = _make_mock_supabase(expected_url)

    with _supabase_patches(mock_supabase):
        resp = await async_client.post(
            "/api/v1/upload/cover",
            headers=headers,
            files={"file": ("photo.jpg", io.BytesIO(b"fake-image-data"), "image/jpeg")},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert "url" in data
    assert data["url"] == expected_url
    # Verify supabase storage was called
    mock_supabase.storage.from_.assert_called_with("covers")


@pytest.mark.asyncio
async def test_upload_cover_no_extension(async_client, register_user, auth_headers):
    """Filename without a dot → falls back to .jpg extension."""
    await register_user(email="upload_noext@example.com")
    headers = await auth_headers(email="upload_noext@example.com")

    expected_url = "https://cdn.example.com/covers/fallback.jpg"
    mock_supabase = _make_mock_supabase(expected_url)

    with _supabase_patches(mock_supabase):
        resp = await async_client.post(
            "/api/v1/upload/cover",
            headers=headers,
            # filename has no "." in it
            files={"file": ("photowithoutext", io.BytesIO(b"fake-image-data"), "image/png")},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert "url" in data
    # Verify the path passed to upload ends with .jpg
    call_args = mock_supabase.storage.from_.return_value.upload.call_args
    path_arg = call_args[0][0]
    assert path_arg.endswith(".jpg"), f"Expected .jpg fallback, got path: {path_arg}"


@pytest.mark.asyncio
async def test_upload_cover_webp(async_client, register_user, auth_headers):
    """WebP images are accepted (allowed content type)."""
    await register_user(email="upload_webp@example.com")
    headers = await auth_headers(email="upload_webp@example.com")

    mock_supabase = _make_mock_supabase("https://cdn.example.com/covers/img.webp")

    with _supabase_patches(mock_supabase):
        resp = await async_client.post(
            "/api/v1/upload/cover",
            headers=headers,
            files={"file": ("image.webp", io.BytesIO(b"RIFF....WEBP"), "image/webp")},
        )

    assert resp.status_code == 200
