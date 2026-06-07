import uuid
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, ConfigDict


class OperatorLoginRequest(BaseModel):
    email: str
    password: str


class OperatorTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class OperatorResponse(BaseModel):
    id: uuid.UUID
    email: str
    full_name: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
