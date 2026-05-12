from datetime import datetime

from pydantic import BaseModel


class MeetingResponse(BaseModel):
    id: str
    clubId: str
    title: str
    date: str  # ISO
    attendees: list[str]  # list of user_id strings


class QuizResponse(BaseModel):
    id: str
    clubId: str
    createdBy: str
    title: str
    description: str | None
    isActive: bool
    status: str = "draft"


class CreateQuizRequest(BaseModel):
    title: str
    description: str | None = None


class UpdateQuizRequest(BaseModel):
    title: str
    description: str | None = None


class QuizQuestionResponse(BaseModel):
    id: str
    quizId: str
    question: str
    options: list[str]
    correctIndex: int | None = None  # only included for organizers
    position: int = 0


class AddQuestionRequest(BaseModel):
    question: str
    options: list[str]
    correctIndex: int


class UpdateQuestionRequest(BaseModel):
    question: str | None = None
    options: list[str] | None = None
    correctIndex: int | None = None


class ReorderQuestionsRequest(BaseModel):
    order: list[str]  # ordered question UUIDs


class SetActiveRequest(BaseModel):
    isActive: bool


class SubmitAttemptRequest(BaseModel):
    answers: list[int]


class AttemptResponse(BaseModel):
    id: str
    quizId: str
    userId: str
    score: int
    total: int
    answers: list[int]


class CreateSessionRequest(BaseModel):
    eventId: str


class QuizSessionResponse(BaseModel):
    id: str
    quizId: str
    eventId: str | None
    startedBy: str
    startedAt: datetime | str
    closedAt: datetime | str | None
    participantCount: int


class LeaderboardEntry(BaseModel):
    rank: int
    userId: str
    displayName: str
    avatarUrl: str | None
    score: int
    totalQuestions: int
    hasAttempted: bool


class LeaderboardResponse(BaseModel):
    entries: list[LeaderboardEntry]
