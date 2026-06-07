import uuid
import logging
from typing import List, Optional
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from src.modules.cart.models import CartItem, EventIdempotencyKey

logger = logging.getLogger("b2b_events")

class EventsService:
    @classmethod
    async def is_event_processed(cls, db: AsyncSession, idempotency_key: uuid.UUID) -> bool:
        """Checks if the event was already processed using the idempotency_key."""
        stmt = select(EventIdempotencyKey).where(EventIdempotencyKey.key == idempotency_key)
        res = await db.execute(stmt)
        return res.scalars().first() is not None

    @classmethod
    async def save_event_idempotency(cls, db: AsyncSession, idempotency_key: uuid.UUID) -> None:
        """Saves the event idempotency key to prevent reprocessing."""
        key_record = EventIdempotencyKey(key=idempotency_key)
        db.add(key_record)
        await db.commit()

    @classmethod
    async def process_b2b_event(
        cls, 
        db: AsyncSession, 
        event_type: str, 
        sku_ids: List[uuid.UUID]
    ) -> None:
        """Processes B2B event, updating unavailable_reason in cart_items."""
        if not sku_ids:
            logger.info("No sku_ids provided in the event. Skipping DB update.")
            return

        # Map event type to unavailable_reason
        reason: Optional[str] = None
        if event_type in ("PRODUCT_BLOCKED", "PRODUCT_HARD_BLOCKED"):
            reason = "PRODUCT_BLOCKED"
        elif event_type == "PRODUCT_DELETED":
            reason = "PRODUCT_DELETED"
        elif event_type == "SKU_OUT_OF_STOCK":
            reason = "OUT_OF_STOCK"

        if reason is None:
            logger.info(f"Event type {event_type} does not affect unavailable_reason. Skipping DB update.")
            return

        # Perform efficient batch update
        stmt = (
            update(CartItem)
            .where(CartItem.sku_id.in_(sku_ids))
            .values(unavailable_reason=reason)
        )
        await db.execute(stmt)
        await db.commit()
        logger.info(f"Successfully batch updated cart items for sku_ids {sku_ids} with reason '{reason}'.")
