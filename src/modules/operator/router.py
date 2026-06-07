import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import (
    OrderAdvanceNotAllowed,
    OrderCancelNotAllowed,
    OrderNotFound,
)
from src.db.database import get_db
from src.modules.operator.auth import get_current_operator
from src.modules.operator.models import Operator
from src.modules.operator.schemas import OperatorLoginRequest, OperatorTokenResponse
from src.modules.operator.service import OperatorService
from src.modules.orders.router import map_order_to_response
from src.modules.orders.schemas import OrderCancelRequest, OrderResponse, PaginatedOrders

router = APIRouter(prefix="/operator", tags=["Operator"])


# ─── Auth ──────────────────────────────────────────────────────────────────────

@router.post(
    "/auth/login",
    response_model=OperatorTokenResponse,
    summary="Вход оператора",
    description="Аутентификация оператора по email и паролю. Возвращает JWT с role=operator.",
)
async def operator_login(
    body: OperatorLoginRequest,
    db: AsyncSession = Depends(get_db),
):
    operator = await OperatorService.authenticate(db, body.email, body.password)
    if not operator:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Неверный email или пароль"},
        )
    token = OperatorService.create_token(operator.id)
    return OperatorTokenResponse(access_token=token)


# ─── Orders (read) ─────────────────────────────────────────────────────────────

@router.get(
    "/orders",
    response_model=PaginatedOrders,
    summary="Список всех заказов (оператор)",
    description="Список заказов с фильтрами по статусу, покупателю и дате создания.",
)
async def list_orders(
    order_status: Optional[str] = Query(None, alias="status"),
    user_id: Optional[uuid.UUID] = Query(None),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    _operator: Operator = Depends(get_current_operator),
    db: AsyncSession = Depends(get_db),
):
    orders, total_count = await OperatorService.get_orders(
        db,
        status=order_status,
        user_id=user_id,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )
    return PaginatedOrders(
        items=[map_order_to_response(o) for o in orders],
        total_count=total_count,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/orders/{order_id}",
    response_model=OrderResponse,
    summary="Детали заказа (оператор)",
)
async def get_order(
    order_id: uuid.UUID,
    _operator: Operator = Depends(get_current_operator),
    db: AsyncSession = Depends(get_db),
):
    order = await OperatorService.get_order(db, order_id)
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "ORDER_NOT_FOUND", "message": "Заказ не найден"},
        )
    return map_order_to_response(order)


# ─── Orders (actions) ──────────────────────────────────────────────────────────

@router.post(
    "/orders/{order_id}/advance",
    response_model=OrderResponse,
    summary="Продвинуть статус заказа",
    description=(
        "Продвигает заказ на один шаг: PAID→ASSEMBLING→DELIVERING→DELIVERED. "
        "Перескочить статус или вернуть назад нельзя. "
        "**Примечание:** при переходе в DELIVERED списание резерва в B2B будет добавлено в US-ORD-05."
    ),
)
async def advance_order_status(
    order_id: uuid.UUID,
    _operator: Operator = Depends(get_current_operator),
    db: AsyncSession = Depends(get_db),
):
    try:
        order = await OperatorService.advance_status(db, order_id)
        return map_order_to_response(order)
    except OrderNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "ORDER_NOT_FOUND", "message": "Заказ не найден"},
        )
    except OrderAdvanceNotAllowed as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "ADVANCE_NOT_ALLOWED",
                "message": f"Смена статуса невозможна: заказ в статусе {e.current_status}",
                "current_status": e.current_status,
            },
        )


@router.post(
    "/orders/{order_id}/cancel",
    response_model=OrderResponse,
    summary="Отменить заказ (оператор)",
    description="Оператор отменяет заказ. Доступно только для PAID и ASSEMBLING.",
)
async def cancel_order(
    order_id: uuid.UUID,
    body: Optional[OrderCancelRequest] = None,
    _operator: Operator = Depends(get_current_operator),
    db: AsyncSession = Depends(get_db),
):
    reason = body.reason if body else None
    try:
        order = await OperatorService.cancel_order(db, order_id, reason)
        return map_order_to_response(order)
    except OrderNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "ORDER_NOT_FOUND", "message": "Заказ не найден"},
        )
    except OrderCancelNotAllowed as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "CANCEL_NOT_ALLOWED",
                "message": f"Отмена невозможна: заказ в статусе {e.current_status}",
                "current_status": e.current_status,
            },
        )
