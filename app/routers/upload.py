import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status

from app.config import get_settings
from app.dependencies import get_current_user
from app.limiter import limiter
from app.models.user import User
from app.services.auth_service import get_supabase_client

router = APIRouter(prefix="/api/v1/upload", tags=["upload"])
logger = structlog.get_logger(__name__)

# S-4: allowlist of accepted MIME types and maximum file size
_ALLOWED_CONTENT_TYPES: frozenset[str] = frozenset({"image/jpeg", "image/png", "image/webp", "image/gif"})
_MAX_FILE_SIZE_BYTES: int = 5 * 1024 * 1024  # 5 MB


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

    contents = await file.read()

    # S-4: validate file size after reading
    if len(contents) > _MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={"error": "File too large. Maximum size is 5 MB", "code": "FILE_TOO_LARGE"},
        )

    try:
        supabase = await get_supabase_client(settings)
        ext = file.filename.rsplit(".", 1)[-1] if file.filename and "." in file.filename else "jpg"
        path = f"covers/{uuid.uuid4()}.{ext}"
        await supabase.storage.from_("covers").upload(
            path, contents, {"content-type": file.content_type or "image/jpeg"}
        )
        url: str = await supabase.storage.from_("covers").get_public_url(path)
    except Exception as exc:
        logger.exception("Supabase storage error", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": "Storage upload failed", "code": "STORAGE_ERROR"},
        ) from exc
    return {"url": url}
