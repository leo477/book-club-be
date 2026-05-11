import asyncio
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.config import get_settings
from app.dependencies import get_current_user
from app.models.user import User

router = APIRouter(prefix="/api/v1/upload", tags=["upload"])


@router.post("/cover")
async def upload_cover(
    file: Annotated[UploadFile, File(...)],
    _current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, str]:
    settings = get_settings()
    if not settings.SUPABASE_URL or not settings.SUPABASE_ANON_KEY:
        raise HTTPException(status_code=503, detail="Storage not configured")

    from supabase import create_client

    ext = file.filename.rsplit(".", 1)[-1] if file.filename and "." in file.filename else "jpg"
    path = f"covers/{uuid.uuid4()}.{ext}"
    contents = await file.read()
    content_type = file.content_type or "image/jpeg"

    # M-6: the sync Supabase client blocks the event loop; offload to a thread
    def _do_upload() -> str:
        supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_ANON_KEY)
        supabase.storage.from_("covers").upload(path, contents, {"content-type": content_type})
        return supabase.storage.from_("covers").get_public_url(path)

    url: str = await asyncio.to_thread(_do_upload)
    return {"url": url}
