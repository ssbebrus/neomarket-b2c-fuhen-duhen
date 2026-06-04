import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import UUID, DateTime, Integer, String, func, CheckConstraint, Index, text
from sqlalchemy.orm import Mapped, mapped_column
from src.db.base import Base

class CartItem(Base):
    __tablename__ = "cart_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid()
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True
    )
    session_id: Mapped[Optional[str]] = mapped_column(
        String,
        nullable=True,
        index=True
    )
    sku_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True
    )
    quantity: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1
    )
    unit_price_at_add: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False
    )

    __table_args__ = (
        CheckConstraint("quantity >= 1", name="check_quantity_min"),
        CheckConstraint("user_id IS NOT NULL OR session_id IS NOT NULL", name="cart_identity"),
        Index("idx_cart_user_sku", "user_id", "sku_id", unique=True, postgresql_where=text("user_id IS NOT NULL")),
        Index("idx_cart_session_sku", "session_id", "sku_id", unique=True, postgresql_where=text("session_id IS NOT NULL")),
    )
