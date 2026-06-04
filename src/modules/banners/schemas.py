from pydantic import BaseModel
from uuid import UUID
from datetime import datetime
from typing import Optional, List, Literal

class BannerResponse(BaseModel):
    id: UUID
    title: Optional[str] = None
    image_url: str
    link: str
    ordering: Optional[int] = None
    active_from: Optional[datetime] = None
    active_to: Optional[datetime] = None

    model_config = {
        "from_attributes": True
    }


class BannerEventCreate(BaseModel):
    banner_id: UUID
    event: Literal["impression", "click"]
    timestamp: datetime


class BannerEventsRequest(BaseModel):
    events: List[BannerEventCreate]


class BannerCreateRequest(BaseModel):
    title: str
    image_url: str
    link: str
    priority: int = 0
    is_active: bool = True
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None

