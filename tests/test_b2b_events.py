import pytest
import uuid
import jwt
from httpx import AsyncClient, Response
from unittest.mock import patch, AsyncMock
from sqlalchemy import select

from src.config import settings
from src.modules.cart.models import CartItem, EventIdempotencyKey
from src.modules.orders.models import Order, OrderItem

USER_ID = uuid.uuid4()
SKU_ID_A = uuid.uuid4()
PRODUCT_ID_A = uuid.uuid4()

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
async def test_product_blocked_marks_cart_items_unavailable(
    client: AsyncClient, 
    test_db, 
    mock_sku_response, 
    mock_product_response
):
    """
    happy: product_blocked_marks_cart_items_unavailable
    - Cart has items with SKU_ID_A
    - B2B sends PRODUCT_BLOCKED event
    - DB cart_items get unavailable_reason = "PRODUCT_BLOCKED"
    - GET /cart returns item with is_available = False and unavailable_reason = "PRODUCT_BLOCKED"
    """
    # 1. Setup CartItem in DB
    item = CartItem(user_id=USER_ID, sku_id=SKU_ID_A, quantity=2, unit_price_at_add=90000)
    test_db.add(item)
    await test_db.commit()

    # 2. Call events endpoint
    event_payload = {
        "idempotency_key": str(uuid.uuid4()),
        "event_type": "PRODUCT_BLOCKED",
        "payload": {
            "product_id": str(PRODUCT_ID_A),
            "sku_ids": [str(SKU_ID_A)],
            "reason": "Описание не соответствует товару"
        },
        "occurred_at": "2026-04-16T12:00:00Z"
    }

    response = await client.post(
        "/api/v1/b2b/events",
        json=event_payload,
        headers={"X-Service-Key": settings.B2B_TO_B2C_KEY}
    )
    assert response.status_code in (200, 202)
    assert response.json()["accepted"] is True

    # 3. Check DB CartItem
    stmt = select(CartItem).where(CartItem.user_id == USER_ID, CartItem.sku_id == SKU_ID_A)
    res = await test_db.execute(stmt)
    db_item = res.scalars().first()
    assert db_item.unavailable_reason == "PRODUCT_BLOCKED"

    # 4. Check Cart Response availability
    with patch("src.modules.catalog.service.CatalogService.get_b2b_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.__aenter__.return_value = mock_client

        mock_sku_resp = AsyncMock(spec=Response)
        mock_sku_resp.status_code = 200
        mock_sku_resp.json = lambda: mock_sku_response
        mock_sku_resp.raise_for_status.return_value = None

        mock_batch_resp = AsyncMock(spec=Response)
        mock_batch_resp.status_code = 200
        mock_batch_resp.json = lambda: [mock_product_response]
        mock_batch_resp.raise_for_status.return_value = None

        mock_client.get.return_value = mock_sku_resp
        mock_client.post.return_value = mock_batch_resp

        token = generate_token(USER_ID)
        cart_resp = await client.get(
            "/api/v1/cart",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert cart_resp.status_code == 200
        cart_data = cart_resp.json()
        assert cart_data["is_valid"] is False
        assert len(cart_data["items"]) == 1
        assert cart_data["items"][0]["is_available"] is False
        assert cart_data["items"][0]["unavailable_reason"] == "PRODUCT_BLOCKED"


@pytest.mark.asyncio
async def test_orders_not_affected_by_product_blocked(client: AsyncClient, test_db):
    """
    happy: orders_not_affected_by_product_blocked
    - Order exists with SKU_ID_A
    - B2B sends PRODUCT_BLOCKED event
    - Order and OrderItem in DB remain unchanged
    """
    # 1. Setup Order in DB
    order_id = uuid.uuid4()
    order = Order(
        id=order_id,
        number="NM-2026-999999",
        buyer_id=USER_ID,
        status="PAID",
        idempotency_key=uuid.uuid4(),
        idempotency_request_body="{}",
        subtotal=10000,
        delivery_cost=0,
        total=10000,
        address_id=uuid.uuid4(),
        payment_method_id=uuid.uuid4()
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
    test_db.add_all([order, item])
    await test_db.commit()

    # 2. Send Blocked Event
    event_payload = {
        "idempotency_key": str(uuid.uuid4()),
        "event_type": "PRODUCT_BLOCKED",
        "payload": {
            "product_id": str(PRODUCT_ID_A),
            "sku_ids": [str(SKU_ID_A)],
            "reason": "Block it"
        },
        "occurred_at": "2026-04-16T12:00:00Z"
    }
    response = await client.post(
        "/api/v1/b2b/events",
        json=event_payload,
        headers={"X-Service-Key": settings.B2B_TO_B2C_KEY}
    )
    assert response.status_code in (200, 202)

    # 3. Verify Order & OrderItem are unchanged
    stmt = select(Order).where(Order.id == order_id)
    res = await test_db.execute(stmt)
    db_order = res.scalars().first()
    assert db_order.status == "PAID"

    stmt_item = select(OrderItem).where(OrderItem.order_id == order_id)
    res_item = await test_db.execute(stmt_item)
    db_item = res_item.scalars().first()
    assert db_item.quantity == 2


@pytest.mark.asyncio
async def test_idempotent_event_no_side_effects(client: AsyncClient, test_db):
    """
    happy: idempotent_event_no_side_effects
    - First send event -> returns success
    - DB updates cart items
    - Manually reset DB cart items unavailable_reason
    - Send event second time with same idempotency_key -> returns success but DB not updated again
    """
    # 1. Setup CartItem in DB
    item = CartItem(user_id=USER_ID, sku_id=SKU_ID_A, quantity=1, unit_price_at_add=90000)
    test_db.add(item)
    await test_db.commit()

    # 2. First send
    idempotency_key = uuid.uuid4()
    event_payload = {
        "idempotency_key": str(idempotency_key),
        "event_type": "PRODUCT_BLOCKED",
        "payload": {
            "product_id": str(PRODUCT_ID_A),
            "sku_ids": [str(SKU_ID_A)]
        },
        "occurred_at": "2026-04-16T12:00:00Z"
    }
    resp1 = await client.post(
        "/api/v1/b2b/events",
        json=event_payload,
        headers={"X-Service-Key": settings.B2B_TO_B2C_KEY}
    )
    assert resp1.status_code in (200, 202)

    # Verify updated
    stmt = select(CartItem).where(CartItem.user_id == USER_ID, CartItem.sku_id == SKU_ID_A)
    res = await test_db.execute(stmt)
    db_item = res.scalars().first()
    assert db_item.unavailable_reason == "PRODUCT_BLOCKED"

    # 3. Manually reset DB cart item state
    db_item.unavailable_reason = None
    await test_db.commit()

    # 4. Second send (same idempotency key)
    resp2 = await client.post(
        "/api/v1/b2b/events",
        json=event_payload,
        headers={"X-Service-Key": settings.B2B_TO_B2C_KEY}
    )
    assert resp2.status_code in (200, 202)

    # 5. Verify DB was not updated again (remains None)
    res_retry = await test_db.execute(stmt)
    db_item_retry = res_retry.scalars().first()
    assert db_item_retry.unavailable_reason is None


@pytest.mark.asyncio
async def test_missing_service_key_returns_401(client: AsyncClient):
    """
    unhappy: missing_service_key_returns_401
    - Call events endpoint without key -> 401
    - Call events endpoint with incorrect key -> 401
    """
    event_payload = {
        "idempotency_key": str(uuid.uuid4()),
        "event_type": "PRODUCT_BLOCKED",
        "payload": {
            "product_id": str(PRODUCT_ID_A)
        },
        "occurred_at": "2026-04-16T12:00:00Z"
    }

    # Missing header
    resp1 = await client.post("/api/v1/b2b/events", json=event_payload)
    assert resp1.status_code == 401
    assert resp1.json()["code"] == "UNAUTHORIZED"

    # Invalid header
    resp2 = await client.post(
        "/api/v1/b2b/events",
        json=event_payload,
        headers={"X-Service-Key": "wrong-key"}
    )
    assert resp2.status_code == 401
    assert resp2.json()["code"] == "UNAUTHORIZED"
