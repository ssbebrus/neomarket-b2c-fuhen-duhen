import uuid
from datetime import datetime
from sqlalchemy import UUID, DateTime, func, UniqueConstraint, ARRAY, String
from sqlalchemy.orm import Mapped, mapped_column
from src.db.base import Base

class Favorite(Base):
    __tablename__ = "favorites"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid()
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        nullable=False
    )

    __table_args__ = (
        UniqueConstraint("user_id", "product_id", name="uq_user_product"),
    )


class ProductSubscription(Base):
    __tablename__ = "product_subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid()
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False
    )
    notify_on: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        nullable=False
    )

    __table_args__ = (
        UniqueConstraint("user_id", "product_id", name="uq_user_product_subscription"),
    )

