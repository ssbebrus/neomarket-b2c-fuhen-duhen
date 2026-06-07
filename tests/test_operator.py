import uuid
import pytest
import jwt
from httpx import AsyncClient, Response
from unittest.mock import patch, AsyncMock
from datetime import datetime, timezone
from sqlalchemy import select

from src.config import settings
from src.modules.operator.models import Operator
from src.modules.operator.service import OperatorService
from src.modules.orders.models import Order, OrderItem

# ─── Fixtures & helpers ────────────────────────────────────────────────────────

OPERATOR_EMAIL = "operator@neomarket.test"
OPERATOR_PASSWORD = "s3cr3t_op"
BUYER_ID = uuid.uuid4()
SKU_ID_A = uuid.uuid4()
PRODUCT_ID_A = uuid.uuid4()
ADDRESS_ID = uuid.uuid4()
PAYMENT_ID = uuid.uuid4()


def make_buyer_token(user_id: uuid.UUID) -> str:
    """Токен покупателя (role отсутствует)."""
    return jwt.encode({"sub": str(user_id)}, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


async def create_operator(db) -> Operator:
    op = Operator(
        email=OPERATOR_EMAIL,
        hashed_password=OperatorService.hash_password(OPERATOR_PASSWORD),
        full_name="Test Operator",
    )
    db.add(op)
    await db.commit()
    return op


async def create_order(db, order_status: str = "PAID") -> Order:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    order = Order(
        number=f"NM-TEST-{uuid.uuid4().hex[:6]}",
        buyer_id=BUYER_ID,
        status=order_status,
        idempotency_key=uuid.uuid4(),
        subtotal=10000,
        total=10000,
        address_id=ADDRESS_ID,
        payment_method_id=PAYMENT_ID,
        status_history={"history": [{"status": order_status, "changed_at": now.isoformat() + "Z", "reason": "test"}]},
        created_at=now,
        updated_at=now,
    )
    db.add(order)
    await db.flush()

    item = OrderItem(
        order_id=order.id,
        sku_id=SKU_ID_A,
        product_id=PRODUCT_ID_A,
        name="Test Product",
        sku_code="SKU-TEST-01",
        quantity=2,
        unit_price=5000,
        line_total=10000,
    )
    db.add(item)
    await db.commit()
    return order


# ─── Auth tests ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_operator_login_success(client: AsyncClient, test_db):
    """happy: правильные email + password → получаем access_token."""
    await create_operator(test_db)

    resp = await client.post(
        "/api/v1/operator/auth/login",
        json={"email": OPERATOR_EMAIL, "password": OPERATOR_PASSWORD},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data

    payload = jwt.decode(data["access_token"], settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    assert payload["role"] == "operator"


@pytest.mark.asyncio
async def test_operator_login_wrong_password(client: AsyncClient, test_db):
    """auth: неверный пароль → 401."""
    await create_operator(test_db)

    resp = await client.post(
        "/api/v1/operator/auth/login",
        json={"email": OPERATOR_EMAIL, "password": "wrongpassword"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_operator_login_unknown_email(client: AsyncClient, test_db):
    """auth: неизвестный email → 401."""
    resp = await client.post(
        "/api/v1/operator/auth/login",
        json={"email": "nobody@test.com", "password": "anything"},
    )
    assert resp.status_code == 401


# ─── Authorization guard tests ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_operator_endpoint_no_token(client: AsyncClient, test_db):
    """auth guard: запрос без токена → 401."""
    resp = await client.get("/api/v1/operator/orders")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_operator_endpoint_buyer_token_rejected(client: AsyncClient, test_db):
    """auth guard: токен покупателя (без role=operator) → 403."""
    token = make_buyer_token(BUYER_ID)
    resp = await client.get(
        "/api/v1/operator/orders",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


# ─── List & Get order tests ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_operator_list_orders(client: AsyncClient, test_db):
    """happy: оператор видит заказы всех покупателей."""
    op = await create_operator(test_db)
    await create_order(test_db, "PAID")
    token = OperatorService.create_token(op.id)

    resp = await client.get(
        "/api/v1/operator/orders",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_count"] >= 1
    assert isinstance(data["items"], list)


@pytest.mark.asyncio
async def test_operator_list_orders_filter_by_status(client: AsyncClient, test_db):
    """filter: фильтр по статусу возвращает только нужные заказы."""
    op = await create_operator(test_db)
    await create_order(test_db, "PAID")
    await create_order(test_db, "ASSEMBLING")
    token = OperatorService.create_token(op.id)

    resp = await client.get(
        "/api/v1/operator/orders?status=PAID",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert all(o["status"] == "PAID" for o in data["items"])


@pytest.mark.asyncio
async def test_operator_get_order_detail(client: AsyncClient, test_db):
    """happy: оператор получает детали конкретного заказа."""
    op = await create_operator(test_db)
    order = await create_order(test_db, "PAID")
    token = OperatorService.create_token(op.id)

    resp = await client.get(
        f"/api/v1/operator/orders/{order.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert str(data["id"]) == str(order.id)
    assert len(data["items"]) == 1


@pytest.mark.asyncio
async def test_operator_get_order_not_found(client: AsyncClient, test_db):
    """not found: несуществующий order_id → 404."""
    op = await create_operator(test_db)
    token = OperatorService.create_token(op.id)

    resp = await client.get(
        f"/api/v1/operator/orders/{uuid.uuid4()}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


# ─── Advance status tests ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_operator_advance_paid_to_assembling(client: AsyncClient, test_db):
    """state machine: PAID → ASSEMBLING."""
    op = await create_operator(test_db)
    order = await create_order(test_db, "PAID")
    token = OperatorService.create_token(op.id)

    resp = await client.post(
        f"/api/v1/operator/orders/{order.id}/advance",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ASSEMBLING"


@pytest.mark.asyncio
async def test_operator_advance_assembling_to_delivering(client: AsyncClient, test_db):
    """state machine: ASSEMBLING → DELIVERING."""
    op = await create_operator(test_db)
    order = await create_order(test_db, "ASSEMBLING")
    token = OperatorService.create_token(op.id)

    resp = await client.post(
        f"/api/v1/operator/orders/{order.id}/advance",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "DELIVERING"


@pytest.mark.asyncio
async def test_operator_advance_delivering_to_delivered(client: AsyncClient, test_db):
    """state machine: DELIVERING → DELIVERED, delivered_at заполнен."""
    op = await create_operator(test_db)
    order = await create_order(test_db, "DELIVERING")
    token = OperatorService.create_token(op.id)

    resp = await client.post(
        f"/api/v1/operator/orders/{order.id}/advance",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "DELIVERED"
    assert data["delivered_at"] is not None


@pytest.mark.asyncio
async def test_operator_advance_terminal_status_rejected(client: AsyncClient, test_db):
    """status guard: попытка продвинуть DELIVERED → 409."""
    op = await create_operator(test_db)
    order = await create_order(test_db, "DELIVERED")
    token = OperatorService.create_token(op.id)

    resp = await client.post(
        f"/api/v1/operator/orders/{order.id}/advance",
        headers={"Authorization": f"Bearer {token}"},
    )
    # Custom exception handler в main.py раскрывает dict напрямую (без обёртки 'detail')
    assert resp.status_code == 409, f"Expected 409, got {resp.status_code}: {resp.json()}"
    assert resp.json().get("code") == "ADVANCE_NOT_ALLOWED", f"Unexpected response: {resp.json()}"


@pytest.mark.asyncio
async def test_operator_advance_cancel_pending_rejected(client: AsyncClient, test_db):
    """status guard: CANCEL_PENDING → 409 (управляется воркером)."""
    op = await create_operator(test_db)
    order = await create_order(test_db, "CANCEL_PENDING")
    token = OperatorService.create_token(op.id)

    resp = await client.post(
        f"/api/v1/operator/orders/{order.id}/advance",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_operator_advance_cancelled_rejected(client: AsyncClient, test_db):
    """status guard: CANCELLED → 409."""
    op = await create_operator(test_db)
    order = await create_order(test_db, "CANCELLED")
    token = OperatorService.create_token(op.id)

    resp = await client.post(
        f"/api/v1/operator/orders/{order.id}/advance",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409


# ─── Operator cancel tests ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_operator_cancel_paid_order_b2b_ok(client: AsyncClient, test_db):
    """happy: оператор отменяет PAID заказ, B2B unreserve ОК → CANCELLED."""
    op = await create_operator(test_db)
    order = await create_order(test_db, "PAID")
    token = OperatorService.create_token(op.id)

    with patch("src.modules.catalog.service.CatalogService.get_b2b_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.__aenter__.return_value = mock_client

        mock_unreserve_resp = AsyncMock(spec=Response)
        mock_unreserve_resp.status_code = 200
        mock_client.post = AsyncMock(return_value=mock_unreserve_resp)

        resp = await client.post(
            f"/api/v1/operator/orders/{order.id}/cancel",
            headers={"Authorization": f"Bearer {token}"},
            json={"reason": "Покупатель не пришёл"},
        )

    assert resp.status_code == 200
    assert resp.json()["status"] == "CANCELLED"


@pytest.mark.asyncio
async def test_operator_cancel_assembling_order(client: AsyncClient, test_db):
    """happy: оператор отменяет ASSEMBLING заказ."""
    op = await create_operator(test_db)
    order = await create_order(test_db, "ASSEMBLING")
    token = OperatorService.create_token(op.id)

    with patch("src.modules.catalog.service.CatalogService.get_b2b_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.__aenter__.return_value = mock_client
        mock_client.post = AsyncMock(return_value=AsyncMock(spec=Response, status_code=200))

        resp = await client.post(
            f"/api/v1/operator/orders/{order.id}/cancel",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    assert resp.json()["status"] == "CANCELLED"


@pytest.mark.asyncio
async def test_operator_cancel_delivering_rejected(client: AsyncClient, test_db):
    """status guard: DELIVERING → нельзя отменить → 409."""
    op = await create_operator(test_db)
    order = await create_order(test_db, "DELIVERING")
    token = OperatorService.create_token(op.id)

    resp = await client.post(
        f"/api/v1/operator/orders/{order.id}/cancel",
        headers={"Authorization": f"Bearer {token}"},
    )
    # Custom exception handler в main.py раскрывает dict напрямую (без обёртки 'detail')
    assert resp.status_code == 409, f"Expected 409, got {resp.status_code}: {resp.json()}"
    assert resp.json().get("code") == "CANCEL_NOT_ALLOWED", f"Unexpected response: {resp.json()}"


@pytest.mark.asyncio
async def test_operator_cancel_b2b_failure_sets_cancel_pending(client: AsyncClient, test_db):
    """edge: B2B unreserve упал → статус CANCEL_PENDING (не CANCELLED)."""
    op = await create_operator(test_db)
    order = await create_order(test_db, "PAID")
    token = OperatorService.create_token(op.id)

    with patch("src.modules.catalog.service.CatalogService.get_b2b_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.__aenter__.return_value = mock_client
        mock_client.post = AsyncMock(side_effect=Exception("B2B is down"))

        resp = await client.post(
            f"/api/v1/operator/orders/{order.id}/cancel",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    assert resp.json()["status"] == "CANCEL_PENDING"
