from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SupportType = Literal["complaint", "suggestion", "comment"]
SupportStatus = Literal["open", "pending", "approved", "rejected", "in_progress", "done"]


class CreateSupportSubmissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: SupportType
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=5000)


class UpdateSupportStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["approved", "rejected", "in_progress", "done"]


class SupportSubmissionResponse(BaseModel):
    id: str
    authorId: str | None = None
    type: SupportType
    title: str
    body: str
    status: SupportStatus
    createdAt: datetime | str
    updatedAt: datetime | str
