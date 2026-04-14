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


class CreateQuizRequest(BaseModel):
    title: str
    description: str | None = None


class QuizQuestionResponse(BaseModel):
    id: str
    quizId: str
    question: str
    options: list[str]
    correctIndex: int | None = None  # only included for organizers


class AddQuestionRequest(BaseModel):
    question: str
    options: list[str]  # exactly 4 options
    correctIndex: int


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
