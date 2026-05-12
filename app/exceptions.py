from __future__ import annotations

from fastapi import HTTPException


class AppError(HTTPException):
    """Structured application error with machine-readable code.

    Usage:
        raise AppError(404, "Club not found", "CLUB_NOT_FOUND")

    Response body:
        {"detail": {"error": "Club not found", "code": "CLUB_NOT_FOUND"}}
    """

    def __init__(self, status_code: int, message: str, code: str) -> None:
        super().__init__(
            status_code=status_code,
            detail={"error": message, "code": code},
        )
