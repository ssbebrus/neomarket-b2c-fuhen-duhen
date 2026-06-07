import uuid
import logging
from datetime import datetime, timezone
from typing import Optional

import jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.modules.operator.models import Operator

logger = logging.getLogger("operator_service")

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

OPERATOR_TRANSITIONS = {
    "PAID": "ASSEMBLING",
    "ASSEMBLING": "DELIVERING",
    "DELIVERING": "DELIVERED",
}

OPERATOR_CANCEL_ALLOWED = {"PAID", "ASSEMBLING"}
TERMINAL_STATUSES = {"DELIVERED", "CANCELLED"}


class OperatorService:

    # ─── Auth ──────────────────────────────────────────────────────────────

    @staticmethod
    def hash_password(password: str) -> str:
        return pwd_context.hash(password)

    @staticmethod
    def verify_password(plain: str, hashed: str) -> bool:
        return pwd_context.verify(plain, hashed)

    @classmethod
    async def authenticate(
        cls,
        db: AsyncSession,
        email: str,
        password: str,
    ) -> Optional[Operator]:
        stmt = select(Operator).where(Operator.email == email)
        res = await db.execute(stmt)
        operator = res.scalars().first()
        if not operator:
            return None
        if not cls.verify_password(password, operator.hashed_password):
            return None
        return operator

    @staticmethod
    def create_token(operator_id: uuid.UUID) -> str:
        payload = {
            "sub": str(operator_id),
            "role": "operator",
        }
        return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)

    # ─── Order management ──────────────────────────────────────────────────

    @staticmethod
    async def get_orders(
        db: AsyncSession,
        status: Optional[str] = None,
        user_id: Optional[uuid.UUID] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        limit: int = 20,
        offset: int = 0,
    ):
        from sqlalchemy import func
        from sqlalchemy.orm import selectinload
        from src.modules.orders.models import Order

        stmt = select(Order)
        if status:
            stmt = stmt.where(Order.status == status)
        if user_id:
            stmt = stmt.where(Order.buyer_id == user_id)
        if date_from:
            stmt = stmt.where(Order.created_at >= date_from)
        if date_to:
            stmt = stmt.where(Order.created_at <= date_to)

        count_stmt = select(func.count()).select_from(stmt.subquery())
        res_count = await db.execute(count_stmt)
        total_count = res_count.scalar() or 0

        stmt = (
            stmt.order_by(Order.created_at.desc())
            .limit(limit)
            .offset(offset)
            .options(selectinload(Order.items))
        )
        res = await db.execute(stmt)
        orders = res.scalars().all()
        return list(orders), total_count

    @staticmethod
    async def get_order(db: AsyncSession, order_id: uuid.UUID):
        from sqlalchemy.orm import selectinload
        from src.modules.orders.models import Order

        stmt = (
            select(Order)
            .where(Order.id == order_id)
            .options(selectinload(Order.items))
        )
        res = await db.execute(stmt)
        return res.scalars().first()

    @classmethod
    async def advance_status(cls, db: AsyncSession, order_id: uuid.UUID):
        """
        Продвигает заказ на один шаг по state machine:
        PAID -> ASSEMBLING -> DELIVERING -> DELIVERED

        При переходе в DELIVERED — только смена статуса.
        Вызов B2B fulfill будет добавлен в следующем PR (US-ORD-05).
        """
        from sqlalchemy.orm import selectinload
        from src.modules.orders.models import Order
        from src.core.exceptions import OrderNotFound, OrderAdvanceNotAllowed

        stmt = (
            select(Order)
            .where(Order.id == order_id)
            .options(selectinload(Order.items))
        )
        res = await db.execute(stmt)
        order = res.scalars().first()

        if not order:
            raise OrderNotFound()

        if order.status in TERMINAL_STATUSES:
            raise OrderAdvanceNotAllowed(current_status=order.status)
        if order.status == "CANCEL_PENDING":
            raise OrderAdvanceNotAllowed(current_status=order.status)
        if order.status == "CANCELLED":
            raise OrderAdvanceNotAllowed(current_status=order.status)

        next_status = OPERATOR_TRANSITIONS.get(order.status)
        if not next_status:
            raise OrderAdvanceNotAllowed(current_status=order.status)

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        order.status = next_status
        order.updated_at = now

        if next_status == "DELIVERED":
            order.delivered_at = now
            # Call B2B fulfill
            from src.modules.catalog.service import CatalogService
            b2b_items = [
                {"sku_id": str(item.sku_id), "quantity": item.quantity}
                for item in order.items
            ]
            try:
                async with await CatalogService.get_b2b_client() as client:
                    resp = await client.post(
                        "/api/v1/inventory/fulfill",
                        json={
                            "order_id": str(order.id),
                            "items": b2b_items
                        }
                    )
                    resp.raise_for_status()
                    order.b2b_fulfilled = True
            except Exception as e:
                logger.error(
                    f"Operator: failed to fulfill order {order.id} in B2B: {e}"
                )

        history_list = order.status_history.get("history", []) if order.status_history else []
        history_list.append({
            "status": next_status,
            "changed_at": now.isoformat() + "Z",
            "reason": f"Оператор сменил статус на {next_status}",
        })
        order.status_history = {"history": history_list}

        await db.commit()

        stmt_refresh = (
            select(Order)
            .where(Order.id == order.id)
            .options(selectinload(Order.items))
        )
        res_refresh = await db.execute(stmt_refresh)
        return res_refresh.scalar_one()

    @classmethod
    async def cancel_order(
        cls,
        db: AsyncSession,
        order_id: uuid.UUID,
        reason: Optional[str] = None,
    ):
        """
        Оператор отменяет заказ (PAID / ASSEMBLING → CANCELLED / CANCEL_PENDING).
        Вызывает B2B unreserve; если B2B недоступен — ставит CANCEL_PENDING.
        """
        import httpx
        from sqlalchemy.orm import selectinload
        from src.modules.orders.models import Order
        from src.modules.catalog.service import CatalogService
        from src.core.exceptions import OrderNotFound, OrderCancelNotAllowed

        stmt = (
            select(Order)
            .where(Order.id == order_id)
            .options(selectinload(Order.items))
        )
        res = await db.execute(stmt)
        order = res.scalars().first()

        if not order:
            raise OrderNotFound()

        if order.status not in OPERATOR_CANCEL_ALLOWED:
            raise OrderCancelNotAllowed(current_status=order.status)

        b2b_items = [
            {"sku_id": str(item.sku_id), "quantity": item.quantity}
            for item in order.items
        ]
        unreserve_success = False

        async with await CatalogService.get_b2b_client() as client:
            try:
                resp = await client.post(
                    "/api/v1/inventory/unreserve",
                    json={"order_id": str(order.id), "items": b2b_items},
                )
                resp.raise_for_status()
                unreserve_success = True
            except Exception as e:
                logger.error(
                    f"Operator: failed to unreserve order {order.id}: {e}"
                )

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        order.status = "CANCELLED" if unreserve_success else "CANCEL_PENDING"
        order.cancel_reason = reason
        order.updated_at = now

        history_list = order.status_history.get("history", []) if order.status_history else []
        history_list.append({
            "status": order.status,
            "changed_at": now.isoformat() + "Z",
            "reason": f"Отмена оператором: {reason}" if reason else "Отмена оператором",
        })
        order.status_history = {"history": history_list}

        await db.commit()

        stmt_refresh = (
            select(Order)
            .where(Order.id == order.id)
            .options(selectinload(Order.items))
        )
        res_refresh = await db.execute(stmt_refresh)
        return res_refresh.scalar_one()
