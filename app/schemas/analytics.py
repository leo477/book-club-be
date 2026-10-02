from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError


class AnalyticsEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app: Literal["angular", "next"]
    name: Literal["cohort", "join_club", "js_error"]
    bucket: str | None = Field(default=None, pattern=r"^\d{1,2}-\d{1,2}$")
    kind: Literal["error", "unhandledrejection", "boundary"] | None = None
    message: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def _error_fields_only_for_js_error(self) -> Self:
        if self.name != "js_error" and (self.kind is not None or self.message is not None):
            raise PydanticCustomError("js_error_only", "kind and message are only allowed for js_error")
        return self
