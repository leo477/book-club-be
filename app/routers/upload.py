import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status

from app.config import get_settings
from app.dependencies import get_current_user
from app.limiter import limiter
from app.models.user import User
from app.services.storage_service import get_storage_client

router = APIRouter(prefix="/api/v1/upload", tags=["upload"])
logger = structlog.get_logger(__name__)

# S-4: allowlist of accepted MIME types and maximum file size
_ALLOWED_CONTENT_TYPES: frozenset[str] = frozenset({"image/jpeg", "image/png", "image/webp", "image/gif"})
_MAX_FILE_SIZE_BYTES: int = 5 * 1024 * 1024  # 5 MB


def _sniff_image_type(data: bytes) -> tuple[str, str] | None:
    """Return (content_type, extension) derived from magic bytes, or None if not an allowed image."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", "jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", "png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif", "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", "webp"
    return None


# noinspection PyUnusedLocal
@router.post("/cover", responses={503: {"description": "Storage not configured"}})
@limiter.limit("10/minute")
async def upload_cover(
    request: Request,  # slowapi requires this exact parameter name
    file: Annotated[UploadFile, File()],
    _current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, str]:
    # S-4: validate content-type
    if file.content_type not in _ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail={"error": "Unsupported file type. Allowed: jpeg, png, webp, gif", "code": "UNSUPPORTED_MEDIA_TYPE"},
        )

    settings = get_settings()
    if not settings.SUPABASE_URL or not settings.SUPABASE_ANON_KEY:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Storage not configured")

    contents = await file.read(_MAX_FILE_SIZE_BYTES + 1)

    # S-4: validate file size after reading
    if len(contents) > _MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={"error": "File too large. Maximum size is 5 MB", "code": "FILE_TOO_LARGE"},
        )

    sniffed = _sniff_image_type(contents)
    if sniffed is None or sniffed[0] != file.content_type:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail={"error": "Unsupported file type. Allowed: jpeg, png, webp, gif", "code": "UNSUPPORTED_MEDIA_TYPE"},
        )
    content_type, ext = sniffed

    storage = get_storage_client(settings)
    if storage is None:
        logger.error("Cover upload unavailable: SUPABASE_SERVICE_ROLE_KEY is not configured")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": "Storage upload failed", "code": "STORAGE_ERROR"},
        )

    try:
        # Bucket is already named "covers", so the object key is just the file name.
        path = f"{uuid.uuid4()}.{ext}"
        await storage.from_("covers").upload(path, contents, {"content-type": content_type})
        url: str = await storage.from_("covers").get_public_url(path)
    except Exception as exc:
        logger.exception("Supabase storage error", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": "Storage upload failed", "code": "STORAGE_ERROR"},
        ) from exc
    return {"url": url}
