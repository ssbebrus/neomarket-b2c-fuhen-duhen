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
