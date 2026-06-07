import uuid
import pytest
from httpx import AsyncClient, Response
from unittest.mock import patch, AsyncMock
from sqlalchemy import select

from src.modules.operator.service import OperatorService
from src.modules.orders.service import OrdersService
from src.modules.orders.models import Order
from tests.test_operator import create_operator, create_order

@pytest.mark.asyncio
async def test_delivered_status_triggers_fulfill_to_b2b(client: AsyncClient, test_db):
    """
    happy path: при DELIVERED вызывается fulfill B2B и выставляется b2b_fulfilled = True.
    """
    op = await create_operator(test_db)
    order = await create_order(test_db, "DELIVERING")
    token = OperatorService.create_token(op.id)

    with patch("src.modules.catalog.service.CatalogService.get_b2b_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.__aenter__.return_value = mock_client

        mock_fulfill_resp = AsyncMock(spec=Response)
        mock_fulfill_resp.status_code = 200
        mock_fulfill_resp.json = lambda: {"fulfilled": True}
        mock_client.post = AsyncMock(return_value=mock_fulfill_resp)

        resp = await client.post(
            f"/api/v1/operator/orders/{order.id}/advance",
            headers={"Authorization": f"Bearer {token}"},
        )

    # Убеждаемся, что статус поменялся на DELIVERED
    assert resp.status_code == 200
    assert resp.json()["status"] == "DELIVERED"

    # Проверяем бд
    stmt = select(Order).where(Order.id == order.id)
    res = await test_db.execute(stmt)
    db_order = res.scalar_one()
    assert db_order.status == "DELIVERED"
    assert db_order.b2b_fulfilled is True

    # Проверяем, что B2B клиент был вызван правильно
    mock_client.post.assert_called_once()
    called_url = mock_client.post.call_args[0][0]
    called_json = mock_client.post.call_args[1]["json"]
    assert called_url == "/api/v1/inventory/fulfill"
    assert called_json["order_id"] == str(order.id)
    assert len(called_json["items"]) == 1
    assert called_json["items"][0]["sku_id"] is not None


@pytest.mark.asyncio
async def test_fulfill_failure_retried_asynchronously(client: AsyncClient, test_db):
    """
    unhappy path: B2B падает → fulfill ретраится асинхронно воркером.
    """
    op = await create_operator(test_db)
    order = await create_order(test_db, "DELIVERING")
    token = OperatorService.create_token(op.id)

    # 1. Сначала B2B недоступен (выбрасывает исключение)
    with patch("src.modules.catalog.service.CatalogService.get_b2b_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.__aenter__.return_value = mock_client
        mock_client.post = AsyncMock(side_effect=Exception("B2B connection error"))

        resp = await client.post(
            f"/api/v1/operator/orders/{order.id}/advance",
            headers={"Authorization": f"Bearer {token}"},
        )

    # Продвижение статуса должно быть успешным, несмотря на сбой B2B
    assert resp.status_code == 200
    assert resp.json()["status"] == "DELIVERED"

    # В БД статус DELIVERED, но b2b_fulfilled = False
    await test_db.refresh(order)
    assert order.status == "DELIVERED"
    assert order.b2b_fulfilled is False

    # 2. B2B восстановился, запускаем фоновую задачу ретрая
    with patch("src.modules.catalog.service.CatalogService.get_b2b_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.__aenter__.return_value = mock_client

        mock_fulfill_resp = AsyncMock(spec=Response)
        mock_fulfill_resp.status_code = 200
        mock_fulfill_resp.json = lambda: {"fulfilled": True}
        mock_client.post = AsyncMock(return_value=mock_fulfill_resp)

        # Вызываем логику обработки незавершенных списаний напрямую
        await OrdersService.process_fulfill_pending(test_db)

    # В БД b2b_fulfilled должно стать True
    await test_db.refresh(order)
    assert order.b2b_fulfilled is True
    mock_client.post.assert_called_once()


@pytest.mark.asyncio
async def test_repeated_fulfill_idempotent(client: AsyncClient, test_db):
    """
    repeated_fulfill_idempotent: повторный вызов с тем же order_id → 200 без изменений.
    """
    op = await create_operator(test_db)
    order = await create_order(test_db, "DELIVERING")
    token = OperatorService.create_token(op.id)

    # 1. Первый вызов — успешный
    with patch("src.modules.catalog.service.CatalogService.get_b2b_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.__aenter__.return_value = mock_client

        mock_fulfill_resp = AsyncMock(spec=Response)
        mock_fulfill_resp.status_code = 200
        mock_fulfill_resp.json = lambda: {"fulfilled": True}
        mock_client.post = AsyncMock(return_value=mock_fulfill_resp)

        resp = await client.post(
            f"/api/v1/operator/orders/{order.id}/advance",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    await test_db.refresh(order)
    assert order.b2b_fulfilled is True

    # Имитируем повторную отправку (например, сбой или ручной ретрай воркером, сбросив флаг)
    order.b2b_fulfilled = False
    await test_db.commit()

    with patch("src.modules.catalog.service.CatalogService.get_b2b_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.__aenter__.return_value = mock_client

        mock_fulfill_resp = AsyncMock(spec=Response)
        # B2B возвращает 200 OK при повторном вызове
        mock_fulfill_resp.status_code = 200
        mock_fulfill_resp.json = lambda: {"fulfilled": True}
        mock_client.post = AsyncMock(return_value=mock_fulfill_resp)

        await OrdersService.process_fulfill_pending(test_db)

    await test_db.refresh(order)
    assert order.b2b_fulfilled is True
    mock_client.post.assert_called_once()
