import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MatterRole = Literal["lead", "partner", "member", "viewer"]


class MatterCreate(BaseModel):
    title: str = Field(min_length=3, max_length=300)
    client_name: str = Field(min_length=1, max_length=300)
    matter_type: str = "litigation"
    forum: str | None = Field(default=None, examples=["Delhi High Court"])
    cnr: str | None = Field(default=None, pattern=r"^[A-Z]{4}\d{12}$", examples=["DLHC010230802020"])
    practice_area: str | None = None


class MatterOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    client_name: str
    matter_type: str
    forum: str | None
    cnr: str | None
    practice_area: str | None
    status: str
    created_at: datetime
    my_role: MatterRole | None = None


class MemberAdd(BaseModel):
    user_id: uuid.UUID
    matter_role: MatterRole = "member"


class MemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: uuid.UUID
    matter_role: MatterRole


class WallCreate(BaseModel):
    user_id: uuid.UUID
    reason: str = Field(min_length=5)
