from pydantic import BaseModel, ConfigDict, Field


class BookOptionResponse(BaseModel):
    id: str
    title: str
    author: str
    votes: int
    hasVoted: bool


class BookVoteRoundResponse(BaseModel):
    id: str
    clubId: str
    status: str
    options: list[BookOptionResponse]
    totalVotes: int
    winnerId: str | None


class AddBookOptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(max_length=300)
    author: str = Field(default="", max_length=300)
