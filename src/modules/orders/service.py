import uuid
import json
import httpx
from datetime import datetime, timezone
from typing import List, Optional
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.exceptions import (
    B2BServiceUnavailable,
    SKUNotFound,
    OrderEmptyCartException,
    OrderIdempotencyConflict,
    OrderSnapshotMismatch,
    OrderReserveFailed,
    OrderNotFound,
    OrderCancelNotAllowed
)
from src.modules.catalog.service import CatalogService
from src.modules.cart.service import CartService
from src.modules.orders.models import Order, OrderItem
from src.modules.orders.schemas import OrderCreateRequest, OrderResponse, OrderItemResponse

class OrdersService:
    @staticmethod
    def _requests_equal(req1: dict, req2: dict) -> bool:
        """
        Deep comparison of two request payloads.
        Only compares relevant keys: address_id, payment_method_id, comment, items_snapshot
        """
        keys = ["address_id", "payment_method_id", "comment", "items_snapshot"]
        for key in keys:
            v1 = req1.get(key)
            v2 = req2.get(key)
            if key == "items_snapshot" and (v1 or v2):
                if not v1 or not v2 or len(v1) != len(v2):
                    return False
                # Sort items by sku_id to compare
                s1 = sorted(v1, key=lambda x: x.get("sku_id", ""))
                s2 = sorted(v2, key=lambda x: x.get("sku_id", ""))
                for i1, i2 in zip(s1, s2):
                    if i1.get("sku_id") != i2.get("sku_id") or i1.get("quantity") != i2.get("quantity") or i1.get("unit_price") != i2.get("unit_price"):
                        return False
            else:
                if str(v1) != str(v2):
                    return False
        return True

    @classmethod
    async def get_order_by_id(cls, db: AsyncSession, order_id: uuid.UUID, buyer_id: uuid.UUID) -> Optional[Order]:
        stmt = select(Order).where(Order.id == order_id, Order.buyer_id == buyer_id).options(selectinload(Order.items))
        res = await db.execute(stmt)
        return res.scalars().first()

    @classmethod
    async def get_orders(
        cls,
        db: AsyncSession,
        buyer_id: uuid.UUID,
        status: Optional[str] = None,
        limit: int = 20,
        offset: int = 0
    ) -> tuple[List[Order], int]:
        stmt = select(Order).where(Order.buyer_id == buyer_id)
        if status:
            stmt = stmt.where(Order.status == status)
        
        # Get total count
        count_stmt = select(func.count()).select_from(stmt.subquery())
        res_count = await db.execute(count_stmt)
        total_count = res_count.scalar() or 0

        # Fetch paginated
        stmt = stmt.order_by(Order.created_at.desc()).limit(limit).offset(offset).options(selectinload(Order.items))
        res = await db.execute(stmt)
        orders = res.scalars().all()
        return list(orders), total_count

    @classmethod
    async def create_order(
        cls,
        db: AsyncSession,
        buyer_id: uuid.UUID,
        idempotency_key: uuid.UUID,
        body: OrderCreateRequest
    ) -> Order:
        # 0. Idempotency Check
        stmt = select(Order).where(Order.idempotency_key == idempotency_key).options(selectinload(Order.items))
        res = await db.execute(stmt)
        existing_order = res.scalars().first()

        current_request_dict = {
            "address_id": str(body.address_id),
            "payment_method_id": str(body.payment_method_id),
            "comment": body.comment,
            "items_snapshot": [
                {
                    "sku_id": str(item.sku_id),
                    "quantity": item.quantity,
                    "unit_price": item.unit_price
                } for item in body.items_snapshot
            ] if body.items_snapshot else None
        }

        if existing_order:
            # Check body match
            try:
                stored_request = json.loads(existing_order.idempotency_request_body) if existing_order.idempotency_request_body else {}
            except Exception:
                stored_request = {}
            
            if cls._requests_equal(stored_request, current_request_dict):
                return existing_order
            else:
                raise OrderIdempotencyConflict()

        # 1. Load cart items
        try:
            enriched_items, is_cart_valid, total_items_count, total_subtotal = await CartService.enrich_cart_items(db, buyer_id, None)
        except B2BServiceUnavailable:
            raise
        except Exception:
            raise B2BServiceUnavailable()

        if not enriched_items:
            raise OrderEmptyCartException()

        # 2. Validate items_snapshot if provided
        if body.items_snapshot:
            # Mismatch detection
            cart_item_map = {item.sku_id: item for item in enriched_items}
            mismatch = len(body.items_snapshot) != len(enriched_items)
            
            if not mismatch:
                for snap_item in body.items_snapshot:
                    cart_item = cart_item_map.get(snap_item.sku_id)
                    if not cart_item or cart_item.quantity != snap_item.quantity or cart_item.unit_price != snap_item.unit_price:
                        mismatch = True
                        break
            
            if mismatch:
                # Construct validation issue response
                issues = []
                for snap_item in body.items_snapshot:
                    cart_item = cart_item_map.get(snap_item.sku_id)
                    if not cart_item:
                        issues.append({
                            "sku_id": str(snap_item.sku_id),
                            "type": "PRODUCT_DELETED",
                            "message": "Product was deleted"
                        })
                    else:
                        if cart_item.unit_price != snap_item.unit_price:
                            issues.append({
                                "sku_id": str(snap_item.sku_id),
                                "type": "PRICE_CHANGED",
                                "message": "Price has changed",
                                "old_value": snap_item.unit_price,
                                "new_value": cart_item.unit_price
                            })
                        if cart_item.quantity != snap_item.quantity:
                            issues.append({
                                "sku_id": str(snap_item.sku_id),
                                "type": "QUANTITY_REDUCED",
                                "message": "Quantity changed",
                                "old_value": snap_item.quantity,
                                "new_value": cart_item.quantity
                            })
                
                # Fetch cart response for validation response
                cart_resp = await CartService.get_cart(db, buyer_id, None)
                raise OrderSnapshotMismatch(cart=cart_resp.model_dump(), issues=issues)

        # 3. Validate availability and stock levels
        failed = []
        for item in enriched_items:
            # Find issues: deleted, blocked, out of stock, insufficient quantity
            if not item.is_available:
                # Determine reason
                # In B2C CartService, is_available is false if product is deleted, blocked or stock <= 0.
                # Let's inspect B2B to distinguish
                # Or we can just default based on available_quantity and is_available status
                if item.available_quantity <= 0:
                    failed.append({
                        "sku_id": str(item.sku_id),
                        "requested": item.quantity,
                        "available": 0,
                        "reason": "OUT_OF_STOCK"
                    })
                else:
                    failed.append({
                        "sku_id": str(item.sku_id),
                        "requested": item.quantity,
                        "available": item.available_quantity,
                        "reason": "PRODUCT_BLOCKED" # or PRODUCT_DELETED
                    })
            elif item.available_quantity < item.quantity:
                failed.append({
                    "sku_id": str(item.sku_id),
                    "requested": item.quantity,
                    "available": item.available_quantity,
                    "reason": "INSUFFICIENT_STOCK"
                })

        if failed:
            raise OrderReserveFailed(failed_items=failed)

        # 4. Generate B2C Order ID first to pass to B2B
        order_id = uuid.uuid4()

        # 5. Call B2B Reserve endpoint
        b2b_items = [{"sku_id": str(item.sku_id), "quantity": item.quantity} for item in enriched_items]
        async with await CatalogService.get_b2b_client() as client:
            try:
                resp = await client.post(
                    "/api/v1/inventory/reserve",
                    json={
                        "idempotency_key": str(idempotency_key),
                        "order_id": str(order_id),
                        "items": b2b_items
                    }
                )
                if resp.status_code == 409:
                    err_json = resp.json()
                    raise OrderReserveFailed(failed_items=err_json.get("failed_items", []))
                resp.raise_for_status()
            except OrderReserveFailed:
                raise
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 409:
                    raise OrderReserveFailed(failed_items=e.response.json().get("failed_items", []))
                raise B2BServiceUnavailable()
            except Exception:
                raise B2BServiceUnavailable()

        # 6. Save order to B2C DB (under transaction)
        # Fetch sequential order number
        stmt_count = select(func.count(Order.id))
        res_count = await db.execute(stmt_count)
        count = res_count.scalar() or 0
        order_number = f"NM-2026-{count + 1:06d}"

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        status_history = [
            {
                "status": "CREATED",
                "changed_at": now.isoformat() + "Z",
                "reason": "Заказ создан"
            },
            {
                "status": "PAID",
                "changed_at": now.isoformat() + "Z",
                "reason": "Оплата прошла успешно (мок)"
            }
        ]

        order = Order(
            id=order_id,
            number=order_number,
            buyer_id=buyer_id,
            status="PAID",
            idempotency_key=idempotency_key,
            idempotency_request_body=json.dumps(current_request_dict),
            subtotal=total_subtotal,
            delivery_cost=0,
            total=total_subtotal,
            address_id=body.address_id,
            payment_method_id=body.payment_method_id,
            comment=body.comment,
            status_history={"history": status_history},
            created_at=now,
            updated_at=now,
            paid_at=now
        )
        db.add(order)

        # Add OrderItems
        for item in enriched_items:
            order_item = OrderItem(
                order_id=order_id,
                sku_id=item.sku_id,
                product_id=item.product_id,
                name=item.name,
                sku_code=item.sku_code,
                quantity=item.quantity,
                unit_price=item.unit_price,
                line_total=item.line_total,
                image_url=item.image.url if item.image else None
            )
            db.add(order_item)

        await db.commit()
        # Eagerly load items relationship before returning
        stmt_refresh = select(Order).where(Order.id == order.id).options(selectinload(Order.items))
        res_refresh = await db.execute(stmt_refresh)
        return res_refresh.scalar_one()

    @classmethod
    async def cancel_order(
        cls,
        db: AsyncSession,
        order_id: uuid.UUID,
        buyer_id: uuid.UUID,
        reason: Optional[str] = None
    ) -> Order:
        # 1. Fetch order (ownership check is built-in)
        stmt = select(Order).where(Order.id == order_id, Order.buyer_id == buyer_id).options(selectinload(Order.items))
        res = await db.execute(stmt)
        order = res.scalars().first()
        if not order:
            raise OrderNotFound()

        # 2. Status verification
        if order.status not in ("CREATED", "PAID"):
            raise OrderCancelNotAllowed(current_status=order.status)

        # 3. Call B2B unreserve
        b2b_items = [{"sku_id": str(item.sku_id), "quantity": item.quantity} for item in order.items]
        unreserve_success = False

        async with await CatalogService.get_b2b_client() as client:
            try:
                resp = await client.post(
                    "/api/v1/inventory/unreserve",
                    json={
                        "order_id": str(order.id),
                        "items": b2b_items
                    }
                )
                resp.raise_for_status()
                unreserve_success = True
            except Exception as e:
                import logging
                logging.getLogger("orders").error(f"Failed to unreserve items in B2B for order {order.id}: {e}")

        # 4. Status transition
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if unreserve_success:
            order.status = "CANCELLED"
        else:
            order.status = "CANCEL_PENDING"

        order.cancel_reason = reason
        order.updated_at = now

        # Add to history
        history_list = order.status_history.get("history", []) if order.status_history else []
        history_list.append({
            "status": order.status,
            "changed_at": now.isoformat() + "Z",
            "reason": f"Отмена заказа: {reason}" if reason else "Отмена заказа"
        })
        order.status_history = {"history": history_list}

        await db.commit()

        # Refresh and return
        stmt_refresh = select(Order).where(Order.id == order.id).options(selectinload(Order.items))
        res_refresh = await db.execute(stmt_refresh)
        return res_refresh.scalar_one()

    worker_check_interval: float = 10.0

    @classmethod
    async def process_cancel_pending(cls, db: AsyncSession) -> None:
        import logging
        logger = logging.getLogger("orders_worker")
        
        # Find all cancel pending orders
        stmt = select(Order).where(Order.status == "CANCEL_PENDING").options(selectinload(Order.items))
        res = await db.execute(stmt)
        orders = res.scalars().all()
        
        for order in orders:
            b2b_items = [{"sku_id": str(item.sku_id), "quantity": item.quantity} for item in order.items]
            try:
                async with await CatalogService.get_b2b_client() as client:
                    resp = await client.post(
                        "/api/v1/inventory/unreserve",
                        json={
                            "order_id": str(order.id),
                            "items": b2b_items
                        }
                    )
                    resp.raise_for_status()
                
                # Success! Transition to CANCELLED
                order.status = "CANCELLED"
                now = datetime.now(timezone.utc).replace(tzinfo=None)
                order.updated_at = now
                
                history_list = order.status_history.get("history", []) if order.status_history else []
                history_list.append({
                    "status": "CANCELLED",
                    "changed_at": now.isoformat() + "Z",
                    "reason": "Автоматическая отмена фоновым воркером"
                })
                order.status_history = {"history": history_list}
                await db.commit()
                logger.info(f"Successfully cancelled order {order.id} via background worker.")
            except Exception as e:
                logger.warning(f"Background retry unreserve failed for order {order.id}: {e}")

    @classmethod
    async def run_cancel_pending_worker(cls) -> None:
        import asyncio
        import logging
        from src.db.database import AsyncSessionLocal
        
        logger = logging.getLogger("orders_worker")
        logger.info("Starting background cancel pending worker...")
        
        while True:
            try:
                await asyncio.sleep(cls.worker_check_interval)
                async with AsyncSessionLocal() as db:
                    await cls.process_cancel_pending(db)
            except asyncio.CancelledError:
                logger.info("Background worker cancelled.")
                break
            except Exception as e:
                logger.error(f"Error in background worker loop: {e}")

    @classmethod
    async def process_fulfill_pending(cls, db: AsyncSession) -> None:
        import logging
        logger = logging.getLogger("orders_worker")
        
        stmt = (
            select(Order)
            .where(Order.status == "DELIVERED", Order.b2b_fulfilled == False)
            .options(selectinload(Order.items))
        )
        res = await db.execute(stmt)
        orders = res.scalars().all()
        
        for order in orders:
            b2b_items = [{"sku_id": str(item.sku_id), "quantity": item.quantity} for item in order.items]
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
                await db.commit()
                logger.info(f"Successfully fulfilled order {order.id} via background worker.")
            except Exception as e:
                logger.warning(f"Background retry fulfill failed for order {order.id}: {e}")

    @classmethod
    async def run_fulfill_worker(cls) -> None:
        import asyncio
        import logging
        from src.db.database import AsyncSessionLocal
        
        logger = logging.getLogger("orders_worker")
        logger.info("Starting background fulfill pending worker...")
        
        while True:
            try:
                await asyncio.sleep(cls.worker_check_interval)
                async with AsyncSessionLocal() as db:
                    await cls.process_fulfill_pending(db)
            except asyncio.CancelledError:
                logger.info("Background worker cancelled.")
                break
            except Exception as e:
                logger.error(f"Error in background worker loop: {e}")


