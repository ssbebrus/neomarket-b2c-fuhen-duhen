import pytest
import uuid
import jwt
from httpx import AsyncClient, Response
from unittest.mock import patch, AsyncMock
from sqlalchemy import select

from src.config import settings
from src.modules.cart.models import CartItem
from src.modules.orders.models import Order, OrderItem

USER_ID = uuid.uuid4()
SKU_ID_A = uuid.uuid4()
SKU_ID_B = uuid.uuid4()
PRODUCT_ID_A = uuid.uuid4()
PRODUCT_ID_B = uuid.uuid4()
ADDRESS_ID = uuid.uuid4()
PAYMENT_METHOD_ID = uuid.uuid4()

def generate_token(user_id: uuid.UUID) -> str:
    payload = {"sub": str(user_id)}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)

@pytest.fixture
def mock_sku_response():
    return {
        "id": str(SKU_ID_A),
        "product_id": str(PRODUCT_ID_A),
        "name": "256GB Black",
        "price": 100000,
        "discount": 10000,
        "active_quantity": 50,
        "article": "SKU-IPHONE-15",
        "images": [],
        "created_at": "2026-06-04T00:00:00Z",
        "updated_at": "2026-06-04T00:00:00Z"
    }

@pytest.fixture
def mock_product_response(mock_sku_response):
    return {
        "id": str(PRODUCT_ID_A),
        "title": "iPhone 15 Pro Max",
        "slug": "iphone-15-pro-max",
        "status": "MODERATED",
        "deleted": False,
        "category_id": str(uuid.uuid4()),
        "seller_id": str(uuid.uuid4()),
        "images": [],
        "characteristics": [],
        "skus": [mock_sku_response],
        "created_at": "2026-06-04T00:00:00Z",
        "updated_at": "2026-06-04T00:00:00Z"
    }

@pytest.mark.asyncio
async def test_checkout_creates_paid_order_with_fixed_prices(client: AsyncClient, test_db, mock_sku_response, mock_product_response):
    """
    happy: checkout_creates_paid_order_with_fixed_prices
    - Cart has 2 items of SKU_ID_A
    - B2B details loaded
    - B2B Reserve returns 200 OK
    - Order is created with status PAID and fixed prices in database
    """
    # 1. Setup cart in DB
    item = CartItem(user_id=USER_ID, sku_id=SKU_ID_A, quantity=2, unit_price_at_add=90000)
    test_db.add(item)
    await test_db.commit()

    token = generate_token(USER_ID)
    idempotency_key = uuid.uuid4()

    with patch("src.modules.catalog.service.CatalogService.get_b2b_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.__aenter__.return_value = mock_client

        # Mock GET SKU details
        mock_sku_resp = AsyncMock(spec=Response)
        mock_sku_resp.status_code = 200
        mock_sku_resp.json = lambda: mock_sku_response
        mock_sku_resp.raise_for_status.return_value = None

        # Mock GET products batch
        mock_batch_resp = AsyncMock(spec=Response)
        mock_batch_resp.status_code = 200
        mock_batch_resp.json = lambda: [mock_product_response]
        mock_batch_resp.raise_for_status.return_value = None

        # Mock POST B2B reserve
        mock_reserve_resp = AsyncMock(spec=Response)
        mock_reserve_resp.status_code = 200
        mock_reserve_resp.json = lambda: {
            "order_id": str(uuid.uuid4()),
            "status": "RESERVED",
            "reserved_at": "2026-06-04T00:00:00Z",
            "reserved": True,
            "items": [{"sku_id": str(SKU_ID_A), "reserved_quantity": 2, "remaining_stock": 48}]
        }
        mock_reserve_resp.raise_for_status.return_value = None

        async def mock_get(url, *args, **kwargs):
            if str(SKU_ID_A) in url:
                return mock_sku_resp
            return AsyncMock(status_code=404)

        async def mock_post(url, *args, **kwargs):
            if "batch" in url:
                return mock_batch_resp
            elif "reserve" in url:
                return mock_reserve_resp
            return AsyncMock(status_code=404)

        mock_client.get.side_effect = mock_get
        mock_client.post.side_effect = mock_post

        # Request body
        req_body = {
            "address_id": str(ADDRESS_ID),
            "payment_method_id": str(PAYMENT_METHOD_ID),
            "comment": "Тестовый комментарий",
            "items_snapshot": [
                {
                    "sku_id": str(SKU_ID_A),
                    "quantity": 2,
                    "unit_price": 90000 # price = 100000 - discount = 10000 -> 90000
                }
            ]
        }

        response = await client.post(
            "/api/v1/orders",
            json=req_body,
            headers={
                "Authorization": f"Bearer {token}",
                "Idempotency-Key": str(idempotency_key)
            }
        )
        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "PAID"
        assert data["subtotal"] == 180000
        assert data["total"] == 180000
        assert len(data["items"]) == 1
        assert data["items"][0]["sku_id"] == str(SKU_ID_A)
        assert data["items"][0]["unit_price"] == 90000
        assert data["items"][0]["name"] == "iPhone 15 Pro Max 256GB Black"

        # Check DB Order
        stmt = select(Order).where(Order.idempotency_key == idempotency_key)
        res = await test_db.execute(stmt)
        db_order = res.scalars().first()
        assert db_order is not None
        assert db_order.status == "PAID"
        assert db_order.subtotal == 180000
        
        # Check DB OrderItem has historical snap
        stmt_item = select(OrderItem).where(OrderItem.order_id == db_order.id)
        res_item = await test_db.execute(stmt_item)
        db_item = res_item.scalars().first()
        assert db_item is not None
        assert db_item.unit_price == 90000
        assert db_item.name == "iPhone 15 Pro Max 256GB Black"
        assert db_item.sku_code == "SKU-IPHONE-15"


@pytest.mark.asyncio
async def test_partial_reserve_failure_returns_409(client: AsyncClient, test_db, mock_sku_response, mock_product_response):
    """
    unhappy: partial_reserve_failure_returns_409
    - Cart has items
    - B2B Reserve returns 409 Conflict with failed_items
    - B2C checkout propagates 409 RESERVE_FAILED
    """
    item = CartItem(user_id=USER_ID, sku_id=SKU_ID_A, quantity=2, unit_price_at_add=90000)
    test_db.add(item)
    await test_db.commit()

    token = generate_token(USER_ID)
    idempotency_key = uuid.uuid4()

    with patch("src.modules.catalog.service.CatalogService.get_b2b_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.__aenter__.return_value = mock_client

        # Mock GET SKU details
        mock_sku_resp = AsyncMock(spec=Response)
        mock_sku_resp.status_code = 200
        mock_sku_resp.json = lambda: mock_sku_response
        mock_sku_resp.raise_for_status.return_value = None

        # Mock GET products batch
        mock_batch_resp = AsyncMock(spec=Response)
        mock_batch_resp.status_code = 200
        mock_batch_resp.json = lambda: [mock_product_response]
        mock_batch_resp.raise_for_status.return_value = None

        # Mock POST B2B reserve returning 409 Conflict
        mock_reserve_resp = AsyncMock(spec=Response)
        mock_reserve_resp.status_code = 409
        mock_reserve_resp.json = lambda: {
            "reserved": False,
            "failed_items": [
                {
                    "sku_id": str(SKU_ID_A),
                    "requested": 2,
                    "available": 1,
                    "reason": "INSUFFICIENT_STOCK"
                }
            ]
        }
        # Simulate raise_for_status raising HTTPStatusError
        import httpx
        request = httpx.Request("POST", "http://test/api/v1/inventory/reserve")
        mock_reserve_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            message="Conflict", request=request, response=mock_reserve_resp
        )

        async def mock_get(url, *args, **kwargs):
            if str(SKU_ID_A) in url:
                return mock_sku_resp
            return AsyncMock(status_code=404)

        async def mock_post(url, *args, **kwargs):
            if "batch" in url:
                return mock_batch_resp
            elif "reserve" in url:
                return mock_reserve_resp
            return AsyncMock(status_code=404)

        mock_client.get.side_effect = mock_get
        mock_client.post.side_effect = mock_post

        req_body = {
            "address_id": str(ADDRESS_ID),
            "payment_method_id": str(PAYMENT_METHOD_ID),
            "comment": "Тестовый комментарий"
        }

        response = await client.post(
            "/api/v1/orders",
            json=req_body,
            headers={
                "Authorization": f"Bearer {token}",
                "Idempotency-Key": str(idempotency_key)
            }
        )
        assert response.status_code == 409
        data = response.json()
        assert data["code"] == "RESERVE_FAILED"
        assert len(data["failed_items"]) == 1
        assert data["failed_items"][0]["sku_id"] == str(SKU_ID_A)
        assert data["failed_items"][0]["reason"] == "INSUFFICIENT_STOCK"

        # Check DB Order is NOT created
        stmt = select(Order).where(Order.idempotency_key == idempotency_key)
        res = await test_db.execute(stmt)
        assert res.scalars().first() is None


@pytest.mark.asyncio
async def test_idempotency_returns_existing_order(client: AsyncClient, test_db, mock_sku_response, mock_product_response):
    """
    unhappy: idempotency_returns_existing_order
    - Repeated POST with same Idempotency-Key returns existing order
    - If request body mismatch, returns 409 Conflict
    """
    item = CartItem(user_id=USER_ID, sku_id=SKU_ID_A, quantity=2, unit_price_at_add=90000)
    test_db.add(item)
    await test_db.commit()

    token = generate_token(USER_ID)
    idempotency_key = uuid.uuid4()

    with patch("src.modules.catalog.service.CatalogService.get_b2b_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.__aenter__.return_value = mock_client

        # Mock GET SKU details
        mock_sku_resp = AsyncMock(spec=Response)
        mock_sku_resp.status_code = 200
        mock_sku_resp.json = lambda: mock_sku_response
        mock_sku_resp.raise_for_status.return_value = None

        # Mock GET products batch
        mock_batch_resp = AsyncMock(spec=Response)
        mock_batch_resp.status_code = 200
        mock_batch_resp.json = lambda: [mock_product_response]
        mock_batch_resp.raise_for_status.return_value = None

        # Mock POST B2B reserve
        mock_reserve_resp = AsyncMock(spec=Response)
        mock_reserve_resp.status_code = 200
        mock_reserve_resp.json = lambda: {
            "order_id": str(uuid.uuid4()),
            "status": "RESERVED",
            "reserved_at": "2026-06-04T00:00:00Z",
            "reserved": True,
            "items": [{"sku_id": str(SKU_ID_A), "reserved_quantity": 2, "remaining_stock": 48}]
        }
        mock_reserve_resp.raise_for_status.return_value = None

        async def mock_get(url, *args, **kwargs):
            if str(SKU_ID_A) in url:
                return mock_sku_resp
            return AsyncMock(status_code=404)

        async def mock_post(url, *args, **kwargs):
            if "batch" in url:
                return mock_batch_resp
            elif "reserve" in url:
                return mock_reserve_resp
            return AsyncMock(status_code=404)

        mock_client.get.side_effect = mock_get
        mock_client.post.side_effect = mock_post

        req_body = {
            "address_id": str(ADDRESS_ID),
            "payment_method_id": str(PAYMENT_METHOD_ID),
            "comment": "Тестовый комментарий"
        }

        # First request
        resp1 = await client.post(
            "/api/v1/orders",
            json=req_body,
            headers={
                "Authorization": f"Bearer {token}",
                "Idempotency-Key": str(idempotency_key)
            }
        )
        assert resp1.status_code == 201
        order_id = resp1.json()["id"]

        # Second request (identical body and key)
        resp2 = await client.post(
            "/api/v1/orders",
            json=req_body,
            headers={
                "Authorization": f"Bearer {token}",
                "Idempotency-Key": str(idempotency_key)
            }
        )
        assert resp2.status_code == 201
        assert resp2.json()["id"] == order_id

        # Third request (different comment)
        req_body_mismatch = req_body.copy()
        req_body_mismatch["comment"] = "Другой комментарий"
        resp3 = await client.post(
            "/api/v1/orders",
            json=req_body_mismatch,
            headers={
                "Authorization": f"Bearer {token}",
                "Idempotency-Key": str(idempotency_key)
            }
        )
        assert resp3.status_code == 409
        assert resp3.json()["code"] == "CONFLICT"


@pytest.mark.asyncio
async def test_b2b_unavailable_returns_503(client: AsyncClient, test_db):
    """
    unhappy: b2b_unavailable_returns_503
    - Cart has items
    - B2B Service throws Exception/Timeout
    - B2C returns 503 B2B_UNAVAILABLE
    """
    item = CartItem(user_id=USER_ID, sku_id=SKU_ID_A, quantity=2, unit_price_at_add=90000)
    test_db.add(item)
    await test_db.commit()

    token = generate_token(USER_ID)
    idempotency_key = uuid.uuid4()

    with patch("src.modules.catalog.service.CatalogService.get_b2b_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.__aenter__.return_value = mock_client

        # Mock GET SKU details raises request exception
        import httpx
        mock_client.get.side_effect = httpx.RequestError("Timeout connection")

        req_body = {
            "address_id": str(ADDRESS_ID),
            "payment_method_id": str(PAYMENT_METHOD_ID),
            "comment": "Тестовый комментарий"
        }

        response = await client.post(
            "/api/v1/orders",
            json=req_body,
            headers={
                "Authorization": f"Bearer {token}",
                "Idempotency-Key": str(idempotency_key)
            }
        )
        assert response.status_code == 503
        data = response.json()
        assert data["code"] == "B2B_UNAVAILABLE"


@pytest.mark.asyncio
async def test_orders_list_returns_own_orders_paginated(client: AsyncClient, test_db):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    order1 = Order(
        id=uuid.uuid4(),
        number="NM-2026-000001",
        buyer_id=USER_ID,
        status="PAID",
        idempotency_key=uuid.uuid4(),
        idempotency_request_body="{}",
        subtotal=10000,
        delivery_cost=0,
        total=10000,
        address_id=ADDRESS_ID,
        payment_method_id=PAYMENT_METHOD_ID,
        created_at=now
    )
    order2 = Order(
        id=uuid.uuid4(),
        number="NM-2026-000002",
        buyer_id=USER_ID,
        status="PAID",
        idempotency_key=uuid.uuid4(),
        idempotency_request_body="{}",
        subtotal=20000,
        delivery_cost=0,
        total=20000,
        address_id=ADDRESS_ID,
        payment_method_id=PAYMENT_METHOD_ID,
        created_at=now
    )
    other_user = uuid.uuid4()
    order_other = Order(
        id=uuid.uuid4(),
        number="NM-2026-000003",
        buyer_id=other_user,
        status="PAID",
        idempotency_key=uuid.uuid4(),
        idempotency_request_body="{}",
        subtotal=30000,
        delivery_cost=0,
        total=30000,
        address_id=ADDRESS_ID,
        payment_method_id=PAYMENT_METHOD_ID,
        created_at=now
    )
    test_db.add_all([order1, order2, order_other])
    await test_db.commit()

    token = generate_token(USER_ID)
    response = await client.get(
        "/api/v1/orders?limit=1&offset=0",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total_count"] == 2
    assert len(data["items"]) == 1
    returned_order = data["items"][0]
    assert returned_order["buyer_id"] == str(USER_ID)
    assert returned_order["id"] in [str(order1.id), str(order2.id)]


@pytest.mark.asyncio
async def test_order_detail_shows_fixed_prices(client: AsyncClient, test_db):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    order_id = uuid.uuid4()
    order = Order(
        id=order_id,
        number="NM-2026-000004",
        buyer_id=USER_ID,
        status="PAID",
        idempotency_key=uuid.uuid4(),
        idempotency_request_body="{}",
        subtotal=90000,
        delivery_cost=0,
        total=90000,
        address_id=ADDRESS_ID,
        payment_method_id=PAYMENT_METHOD_ID,
        created_at=now
    )
    item = OrderItem(
        order_id=order_id,
        sku_id=SKU_ID_A,
        product_id=PRODUCT_ID_A,
        name="iPhone 15 Pro Max 256GB Black",
        sku_code="SKU-IPHONE-15",
        quantity=1,
        unit_price=90000,
        line_total=90000
    )
    test_db.add(order)
    test_db.add(item)
    await test_db.commit()

    token = generate_token(USER_ID)
    response = await client.get(
        f"/api/v1/orders/{order_id}",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(order_id)
    assert len(data["items"]) == 1
    assert data["items"][0]["unit_price"] == 90000


@pytest.mark.asyncio
async def test_other_user_order_returns_404_not_403(client: AsyncClient, test_db):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    other_user_id = uuid.uuid4()
    order_id = uuid.uuid4()
    order = Order(
        id=order_id,
        number="NM-2026-000005",
        buyer_id=other_user_id,
        status="PAID",
        idempotency_key=uuid.uuid4(),
        idempotency_request_body="{}",
        subtotal=5000,
        delivery_cost=0,
        total=5000,
        address_id=ADDRESS_ID,
        payment_method_id=PAYMENT_METHOD_ID,
        created_at=now
    )
    test_db.add(order)
    await test_db.commit()

    token = generate_token(USER_ID)
    response = await client.get(
        f"/api/v1/orders/{order_id}",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 404
    data = response.json()
    assert data["code"] == "ORDER_NOT_FOUND"


@pytest.mark.asyncio
async def test_cancel_paid_order_transitions_to_cancelled(client: AsyncClient, test_db):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    order_id = uuid.uuid4()
    order = Order(
        id=order_id,
        number="NM-2026-000006",
        buyer_id=USER_ID,
        status="PAID",
        idempotency_key=uuid.uuid4(),
        idempotency_request_body="{}",
        subtotal=10000,
        delivery_cost=0,
        total=10000,
        address_id=ADDRESS_ID,
        payment_method_id=PAYMENT_METHOD_ID,
        created_at=now
    )
    item = OrderItem(
        order_id=order_id,
        sku_id=SKU_ID_A,
        product_id=PRODUCT_ID_A,
        name="iPhone 15 Pro Max 256GB Black",
        sku_code="SKU-IPHONE-15",
        quantity=2,
        unit_price=5000,
        line_total=10000
    )
    test_db.add(order)
    test_db.add(item)
    await test_db.commit()

    token = generate_token(USER_ID)
    with patch("src.modules.catalog.service.CatalogService.get_b2b_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.__aenter__.return_value = mock_client

        # Mock B2B unreserve returning 200 OK
        mock_unreserve_resp = AsyncMock(spec=Response)
        mock_unreserve_resp.status_code = 200
        mock_unreserve_resp.json = lambda: {
            "unreserved": True,
            "items": [{"sku_id": str(SKU_ID_A), "unreserved_quantity": 2, "remaining_stock": 50}]
        }
        mock_unreserve_resp.raise_for_status.return_value = None
        mock_client.post.return_value = mock_unreserve_resp

        response = await client.post(
            f"/api/v1/orders/{order_id}/cancel",
            json={"reason": "Клиент передумал"},
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "CANCELLED"
        assert data["cancel_reason"] == "Клиент передумал"

        # Check mock call path
        mock_client.post.assert_called_once_with(
            "/api/v1/inventory/unreserve",
            json={
                "order_id": str(order_id),
                "items": [{"sku_id": str(SKU_ID_A), "quantity": 2}]
            }
        )

        # Check DB
        stmt = select(Order).where(Order.id == order_id)
        res = await test_db.execute(stmt)
        db_order = res.scalars().first()
        assert db_order.status == "CANCELLED"


@pytest.mark.asyncio
async def test_unreserve_failure_transitions_to_cancel_pending(client: AsyncClient, test_db):
    from datetime import datetime, timezone
    import asyncio
    from src.modules.orders.service import OrdersService
    
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    order_id = uuid.uuid4()
    order = Order(
        id=order_id,
        number="NM-2026-000007",
        buyer_id=USER_ID,
        status="PAID",
        idempotency_key=uuid.uuid4(),
        idempotency_request_body="{}",
        subtotal=10000,
        delivery_cost=0,
        total=10000,
        address_id=ADDRESS_ID,
        payment_method_id=PAYMENT_METHOD_ID,
        created_at=now
    )
    item = OrderItem(
        order_id=order_id,
        sku_id=SKU_ID_A,
        product_id=PRODUCT_ID_A,
        name="iPhone 15 Pro Max 256GB Black",
        sku_code="SKU-IPHONE-15",
        quantity=2,
        unit_price=5000,
        line_total=10000
    )
    test_db.add(order)
    test_db.add(item)
    await test_db.commit()

    token = generate_token(USER_ID)
    with patch("src.modules.catalog.service.CatalogService.get_b2b_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.__aenter__.return_value = mock_client
 
        # Mock B2B unreserve throws exception (B2B down)
        mock_client.post.side_effect = Exception("Connection Timeout")
 
        response = await client.post(
            f"/api/v1/orders/{order_id}/cancel",
            headers={"Authorization": f"Bearer {token}"}
        )
        # B2C-11 specifies it transitions to CANCEL_PENDING but still returns 200 OK
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "CANCEL_PENDING"
 
        # Check mock call path
        mock_client.post.assert_called_once_with(
            "/api/v1/inventory/unreserve",
            json={
                "order_id": str(order_id),
                "items": [{"sku_id": str(SKU_ID_A), "quantity": 2}]
            }
        )

        # Check DB
        stmt = select(Order).where(Order.id == order_id)
        res = await test_db.execute(stmt)
        db_order = res.scalars().first()
        assert db_order.status == "CANCEL_PENDING"
 
    # Now verify the background worker picks it up and retries successfully
    with patch("src.modules.catalog.service.CatalogService.get_b2b_client") as mock_get_client_worker:
        mock_client_worker = AsyncMock()
        mock_get_client_worker.return_value = mock_client_worker
        mock_client_worker.__aenter__.return_value = mock_client_worker
        
        mock_unreserve_resp = AsyncMock(spec=Response)
        mock_unreserve_resp.status_code = 200
        mock_unreserve_resp.json = lambda: {"unreserved": True}
        mock_unreserve_resp.raise_for_status.return_value = None
        mock_client_worker.post.return_value = mock_unreserve_resp
 
        # Call process_cancel_pending directly using the test_db session
        await OrdersService.process_cancel_pending(test_db)
 
        # Check mock call path in worker
        mock_client_worker.post.assert_called_once_with(
            "/api/v1/inventory/unreserve",
            json={
                "order_id": str(order_id),
                "items": [{"sku_id": str(SKU_ID_A), "quantity": 2}]
            }
        )

        # Check DB: order status must be CANCELLED now
        stmt = select(Order).where(Order.id == order_id)
        res = await test_db.execute(stmt)
        db_order = res.scalars().first()
        assert db_order.status == "CANCELLED"



@pytest.mark.asyncio
async def test_cancel_assembling_order_returns_409(client: AsyncClient, test_db):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    order_id = uuid.uuid4()
    order = Order(
        id=order_id,
        number="NM-2026-000008",
        buyer_id=USER_ID,
        status="ASSEMBLING",
        idempotency_key=uuid.uuid4(),
        idempotency_request_body="{}",
        subtotal=10000,
        delivery_cost=0,
        total=10000,
        address_id=ADDRESS_ID,
        payment_method_id=PAYMENT_METHOD_ID,
        created_at=now
    )
    test_db.add(order)
    await test_db.commit()

    token = generate_token(USER_ID)
    response = await client.post(
        f"/api/v1/orders/{order_id}/cancel",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 409
    data = response.json()
    assert data["code"] == "CANCEL_NOT_ALLOWED"
    assert data["current_status"] == "ASSEMBLING"


@pytest.mark.asyncio
async def test_other_user_order_returns_404_on_cancel(client: AsyncClient, test_db):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    other_user_id = uuid.uuid4()
    order_id = uuid.uuid4()
    order = Order(
        id=order_id,
        number="NM-2026-000009",
        buyer_id=other_user_id,
        status="PAID",
        idempotency_key=uuid.uuid4(),
        idempotency_request_body="{}",
        subtotal=5000,
        delivery_cost=0,
        total=5000,
        address_id=ADDRESS_ID,
        payment_method_id=PAYMENT_METHOD_ID,
        created_at=now
    )
    test_db.add(order)
    await test_db.commit()

    token = generate_token(USER_ID)
    response = await client.post(
        f"/api/v1/orders/{order_id}/cancel",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 404
    data = response.json()
    assert data["code"] == "ORDER_NOT_FOUND"

