import io
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


SERVICE_KEY = "service-role-secret-value"


def _make_mock_supabase(public_url: str = "https://cdn.example.com/covers/test.jpg") -> MagicMock:
    mock_supabase = MagicMock()
    mock_supabase.storage.from_.return_value.upload = AsyncMock(return_value=None)
    mock_supabase.storage.from_.return_value.get_public_url = AsyncMock(return_value=public_url)
    return mock_supabase


def _make_mock_settings() -> MagicMock:
    """Return a settings mock that looks configured (non-empty Supabase credentials)."""
    s = MagicMock()
    s.SUPABASE_URL = "https://test.supabase.co"
    s.SUPABASE_ANON_KEY = "test-anon-key"
    s.SUPABASE_SERVICE_ROLE_KEY = SERVICE_KEY
    return s


@contextmanager
def _supabase_patches(mock_supabase: MagicMock):
    """Patch both get_settings (so 503 branch is skipped) and the service-role storage client factory."""
    with (
        patch("app.routers.upload.get_settings", return_value=_make_mock_settings()),
        patch("app.routers.upload.get_storage_client", return_value=mock_supabase.storage),
    ):
        yield


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16
GIF = b"GIF89a" + b"\x00" * 16
WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 8

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_cover_unauthenticated(async_client):
    """No auth token → 401."""
    resp = await async_client.post(
        "/api/v1/upload/cover",
        files={"file": ("photo.jpg", io.BytesIO(JPEG), "image/jpeg")},
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

    large_content = JPEG + b"x" * (5 * 1024 * 1024)  # 5 MB + 1 byte
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
    mock_supabase.storage.from_.return_value.upload.assert_not_called()


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
            files={"file": ("photo.jpg", io.BytesIO(JPEG), "image/jpeg")},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert "url" in data
    assert data["url"] == expected_url
    # Verify supabase storage was called
    mock_supabase.storage.from_.assert_called_with("covers")


@pytest.mark.asyncio
async def test_upload_cover_no_extension(async_client, register_user, auth_headers):
    """Filename without a dot → extension comes from the sniffed type."""
    await register_user(email="upload_noext@example.com")
    headers = await auth_headers(email="upload_noext@example.com")

    expected_url = "https://cdn.example.com/covers/fallback.jpg"
    mock_supabase = _make_mock_supabase(expected_url)

    with _supabase_patches(mock_supabase):
        resp = await async_client.post(
            "/api/v1/upload/cover",
            headers=headers,
            # filename has no "." in it
            files={"file": ("photowithoutext", io.BytesIO(PNG), "image/png")},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert "url" in data
    call_args = mock_supabase.storage.from_.return_value.upload.call_args
    assert call_args[0][0].endswith(".png")


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
            files={"file": ("image.webp", io.BytesIO(WEBP), "image/webp")},
        )

    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_upload_cover_storage_not_configured(async_client, register_user, auth_headers):
    """Missing Supabase credentials → 503."""
    await register_user(email="upload_503@example.com")
    headers = await auth_headers(email="upload_503@example.com")

    empty_settings = MagicMock()
    empty_settings.SUPABASE_URL = ""
    empty_settings.SUPABASE_ANON_KEY = ""

    with patch("app.routers.upload.get_settings", return_value=empty_settings):
        resp = await async_client.post(
            "/api/v1/upload/cover",
            headers=headers,
            files={"file": ("photo.jpg", io.BytesIO(JPEG), "image/jpeg")},
        )

    assert resp.status_code == 503
    assert "Storage not configured" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_upload_cover_supabase_error(async_client, register_user, auth_headers):
    """Supabase upload raises → 502 with STORAGE_ERROR code."""
    await register_user(email="upload_502@example.com")
    headers = await auth_headers(email="upload_502@example.com")

    broken_supabase = MagicMock()
    broken_supabase.storage.from_.return_value.upload.side_effect = RuntimeError("bucket not found")

    with _supabase_patches(broken_supabase):
        resp = await async_client.post(
            "/api/v1/upload/cover",
            headers=headers,
            files={"file": ("photo.jpg", io.BytesIO(JPEG), "image/jpeg")},
        )

    assert resp.status_code == 502
    detail = resp.json()["detail"]
    assert detail["code"] == "STORAGE_ERROR"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("data", "content_type", "ext"),
    [
        (PNG, "image/png", ".png"),
        (JPEG, "image/jpeg", ".jpg"),
        (GIF, "image/gif", ".gif"),
        (WEBP, "image/webp", ".webp"),
    ],
)
async def test_upload_cover_sniffed_types(async_client, register_user, auth_headers, data, content_type, ext):
    email = f"upload_sniff_{ext[1:]}@example.com"
    await register_user(email=email)
    headers = await auth_headers(email=email)
    mock_supabase = _make_mock_supabase()

    with _supabase_patches(mock_supabase):
        resp = await async_client.post(
            "/api/v1/upload/cover",
            headers=headers,
            files={"file": ("whatever.bin", io.BytesIO(data), content_type)},
        )

    assert resp.status_code == 200
    call_args = mock_supabase.storage.from_.return_value.upload.call_args
    assert call_args[0][0].endswith(ext)
    assert call_args[0][2] == {"content-type": content_type}


@pytest.mark.asyncio
async def test_upload_cover_html_with_image_header_rejected(async_client, register_user, auth_headers):
    await register_user(email="upload_html@example.com")
    headers = await auth_headers(email="upload_html@example.com")
    mock_supabase = _make_mock_supabase()

    with _supabase_patches(mock_supabase):
        resp = await async_client.post(
            "/api/v1/upload/cover",
            headers=headers,
            files={"file": ("x.html", io.BytesIO(b"<html><script>alert(1)</script></html>"), "image/png")},
        )

    assert resp.status_code == 415
    assert resp.json()["detail"]["code"] == "UNSUPPORTED_MEDIA_TYPE"
    mock_supabase.storage.from_.return_value.upload.assert_not_called()


@pytest.mark.asyncio
async def test_upload_cover_declared_type_mismatch_rejected(async_client, register_user, auth_headers):
    await register_user(email="upload_mismatch@example.com")
    headers = await auth_headers(email="upload_mismatch@example.com")

    mock_supabase = _make_mock_supabase()

    with _supabase_patches(mock_supabase):
        resp = await async_client.post(
            "/api/v1/upload/cover",
            headers=headers,
            files={"file": ("a.jpg", io.BytesIO(PNG), "image/jpeg")},
        )

    assert resp.status_code == 415
    assert resp.json()["detail"]["code"] == "UNSUPPORTED_MEDIA_TYPE"
    mock_supabase.storage.from_.return_value.upload.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("data", "content_type"),
    [
        (b"", "image/png"),
        (b"RIFF\x00\x00WEB", "image/webp"),
        (b"RIFF\x00\x00\x00\x00WAVE" + b"\x00" * 8, "image/webp"),
        (b'<svg xmlns="http://www.w3.org/2000/svg"></svg>', "image/svg+xml"),
    ],
    ids=["empty", "short-riff", "riff-wave", "svg"],
)
async def test_upload_cover_non_image_content_rejected(async_client, register_user, auth_headers, data, content_type):
    email = f"upload_reject_{abs(hash((data, content_type)))}@example.com"
    await register_user(email=email)
    headers = await auth_headers(email=email)
    mock_supabase = _make_mock_supabase()

    with _supabase_patches(mock_supabase):
        resp = await async_client.post(
            "/api/v1/upload/cover",
            headers=headers,
            files={"file": ("x.bin", io.BytesIO(data), content_type)},
        )

    assert resp.status_code == 415
    assert resp.json()["detail"]["code"] == "UNSUPPORTED_MEDIA_TYPE"
    mock_supabase.storage.from_.return_value.upload.assert_not_called()


@pytest.mark.asyncio
async def test_upload_cover_extension_from_sniffed_type(async_client, register_user, auth_headers):
    await register_user(email="upload_ext@example.com")
    headers = await auth_headers(email="upload_ext@example.com")
    mock_supabase = _make_mock_supabase()

    with _supabase_patches(mock_supabase):
        resp = await async_client.post(
            "/api/v1/upload/cover",
            headers=headers,
            files={"file": ("x.html", io.BytesIO(PNG), "image/png")},
        )

    assert resp.status_code == 200
    path_arg = mock_supabase.storage.from_.return_value.upload.call_args[0][0]
    assert path_arg.endswith(".png")
    assert "html" not in path_arg


@pytest.mark.asyncio
async def test_upload_cover_missing_service_key_fails_closed(async_client, register_user, auth_headers, caplog):
    await register_user(email="upload_nokey@example.com")
    headers = await auth_headers(email="upload_nokey@example.com")

    settings = _make_mock_settings()
    settings.SUPABASE_SERVICE_ROLE_KEY = ""

    with (
        patch("app.routers.upload.get_settings", return_value=settings),
        patch("app.services.auth_service.get_supabase_client", AsyncMock()) as anon_factory,
    ):
        resp = await async_client.post(
            "/api/v1/upload/cover",
            headers=headers,
            files={"file": ("photo.jpg", io.BytesIO(JPEG), "image/jpeg")},
        )

    assert resp.status_code == 502
    assert resp.json()["detail"]["code"] == "STORAGE_ERROR"
    anon_factory.assert_not_called()


@pytest.mark.asyncio
async def test_upload_cover_uses_service_role_client_and_hides_key(async_client, register_user, auth_headers, caplog):
    await register_user(email="upload_svc@example.com")
    headers = await auth_headers(email="upload_svc@example.com")
    mock_supabase = _make_mock_supabase("https://x.supabase.co/storage/v1/object/public/covers/a.jpg")

    with (
        _supabase_patches(mock_supabase),
        patch("app.services.auth_service.get_supabase_client", AsyncMock()) as anon_factory,
    ):
        resp = await async_client.post(
            "/api/v1/upload/cover",
            headers=headers,
            files={"file": ("photo.jpg", io.BytesIO(JPEG), "image/jpeg")},
        )

    assert resp.status_code == 200
    assert resp.json() == {"url": "https://x.supabase.co/storage/v1/object/public/covers/a.jpg"}
    anon_factory.assert_not_called()
    path = mock_supabase.storage.from_.return_value.upload.call_args[0][0]
    assert path.count("/") == 0
    assert path.endswith(".jpg")
    assert SERVICE_KEY not in resp.text
    assert SERVICE_KEY not in caplog.text


@pytest.mark.asyncio
async def test_upload_cover_storage_error_does_not_leak_key(async_client, register_user, auth_headers, caplog):
    await register_user(email="upload_err@example.com")
    headers = await auth_headers(email="upload_err@example.com")
    broken = MagicMock()
    broken.storage.from_.return_value.upload = AsyncMock(side_effect=RuntimeError("bucket not found"))

    with _supabase_patches(broken):
        resp = await async_client.post(
            "/api/v1/upload/cover",
            headers=headers,
            files={"file": ("photo.jpg", io.BytesIO(JPEG), "image/jpeg")},
        )

    assert resp.status_code == 502
    assert SERVICE_KEY not in resp.text
    assert SERVICE_KEY not in caplog.text
