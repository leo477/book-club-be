from typing import Literal

from pydantic import BaseModel, ConfigDict


class RegisterPushTokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str
    platform: Literal["ios", "android"]


class UnregisterPushTokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str
