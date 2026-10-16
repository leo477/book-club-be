from typing import Annotated
from urllib.parse import urlsplit

from pydantic import AfterValidator
from pydantic_core import PydanticCustomError

_MAX_URL_LENGTH = 500


def _invalid(message: str) -> PydanticCustomError:
    # PydanticCustomError keeps the 422 body JSON-serializable (a ValueError lands in ctx unserialized)
    return PydanticCustomError("invalid_cover_url", message)


def _validate_cover_url(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    if len(value) > _MAX_URL_LENGTH:
        raise _invalid("URL is too long")
    if "\\" in value or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise _invalid("URL must not contain whitespace or control characters")
    try:
        parts = urlsplit(value)
        host = parts.hostname
        _ = parts.port
    except ValueError as exc:
        raise _invalid("Invalid URL") from exc
    if parts.scheme.lower() not in ("http", "https") or not host:
        raise _invalid("URL must be an http(s) URL with a host")
    if parts.username is not None or parts.password is not None:
        raise _invalid("URL must not contain credentials")
    return value


CoverUrl = Annotated[str | None, AfterValidator(_validate_cover_url)]
