import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import UUID, DateTime, Integer, String, Boolean, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column
from src.db.base import Base

class Banner(Base):
    __tablename__ = "banners"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid()
    )
    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False
    )
    image_url: Mapped[str] = mapped_column(
        String(500),
        nullable=False
    )
    link: Mapped[str] = mapped_column(
        String(500),
        nullable=False
    )
    priority: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true"
    )
    start_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime,
        nullable=True
    )
    end_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime,
        nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now()
    )


class BannerEvent(Base):
    __tablename__ = "banner_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid()
    )
    banner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("banners.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True
    )
    event: Mapped[str] = mapped_column(
        String(20),
        nullable=False
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now()
    )
