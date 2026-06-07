import uuid
from typing import Optional, List
from fastapi import APIRouter, Depends, Header, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.database import get_db
from src.modules.favorites.router import get_current_user_id
from src.core.exceptions import (
    B2BServiceUnavailable,
    OrderEmptyCartException,
    OrderIdempotencyConflict,
    OrderSnapshotMismatch,
    OrderReserveFailed
)
from src.modules.orders.service import OrdersService
from src.modules.orders.schemas import (
    OrderCreateRequest,
    OrderResponse,
    PaginatedOrders,
    AddressResponse,
    PaymentMethodResponse,
    StatusHistoryItem
)

router = APIRouter(prefix="/orders", tags=["Orders"])

def map_order_to_response(order) -> OrderResponse:
    history_dict = order.status_history or {}
    history_list = history_dict.get("history", [])
    
    status_history = [
        StatusHistoryItem(
            status=h.get("status"),
            changed_at=h.get("changed_at"),
            reason=h.get("reason")
        ) for h in history_list
    ]

    return OrderResponse(
        id=order.id,
        number=order.number,
        buyer_id=order.buyer_id,
        status=order.status,
        status_history=status_history,
        items=[
            {
                "sku_id": item.sku_id,
                "product_id": item.product_id,
                "name": item.name,
                "sku_code": item.sku_code,
                "quantity": item.quantity,
                "unit_price": item.unit_price,
                "line_total": item.line_total,
                "image_url": item.image_url
            } for item in order.items
        ],
        subtotal=order.subtotal,
        delivery_cost=order.delivery_cost,
        total=order.total,
        address=AddressResponse(id=order.address_id),
        payment_method=PaymentMethodResponse(id=order.payment_method_id),
        comment=order.comment,
        cancel_reason=order.cancel_reason,
        created_at=order.created_at,
        paid_at=order.paid_at,
        delivered_at=order.delivered_at
    )

@router.post(
    "",
    response_model=OrderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Создать заказ (чекаут)"
)
async def checkout(
    body: OrderCreateRequest,
    idempotency_key: uuid.UUID = Header(..., alias="Idempotency-Key"),
    buyer_id: uuid.UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    try:
        order = await OrdersService.create_order(db, buyer_id, idempotency_key, body)
        return map_order_to_response(order)
    except OrderEmptyCartException:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_REQUEST", "message": "Список items не может быть пустым"}
        )
    except OrderIdempotencyConflict:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "CONFLICT",
                "message": "Конфликт: несовпадение тела с уже использованным idempotency_key"
            }
        )
    except OrderSnapshotMismatch as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "is_valid": False,
                "cart": e.cart,
                "issues": e.issues
            }
        )
    except OrderReserveFailed as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "RESERVE_FAILED",
                "message": "Не удалось зарезервировать товары",
                "failed_items": e.failed_items
            }
        )
    except B2BServiceUnavailable:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "B2B_UNAVAILABLE", "message": "Сервис товаров временно недоступен, попробуйте позже"}
        )


@router.get(
    "",
    response_model=PaginatedOrders,
    summary="История заказов покупателя"
)
async def list_orders(
    status: Optional[str] = Query(None, description="Фильтр по статусу заказа"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    buyer_id: uuid.UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    orders, total_count = await OrdersService.get_orders(db, buyer_id, status, limit, offset)
    items = [map_order_to_response(order) for order in orders]
    return PaginatedOrders(
        items=items,
        total_count=total_count,
        limit=limit,
        offset=offset
    )


@router.get(
    "/{order_id}",
    response_model=OrderResponse,
    summary="Карточка заказа"
)
async def get_order(
    order_id: uuid.UUID,
    buyer_id: uuid.UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    order = await OrdersService.get_order_by_id(db, order_id, buyer_id)
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Заказ не найден"}
        )
    return map_order_to_response(order)
