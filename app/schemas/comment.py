from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import SafeStr
from app.schemas.task import UserShort


class CommentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    # Stripped before length check -> whitespace-only text is rejected.
    text: SafeStr = Field(min_length=1, max_length=5000)


class CommentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    task_id: int
    author: UserShort
    text: str
    created_at: datetime
