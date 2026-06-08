import pytest
import uuid
import jwt
from httpx import AsyncClient, Response
from unittest.mock import patch, AsyncMock
from sqlalchemy import select

from src.config import settings
from src.modules.cart.models import CartItem

USER_ID = uuid.uuid4()
SESSION_ID = str(uuid.uuid4())
SKU_ID_A = uuid.uuid4()
SKU_ID_B = uuid.uuid4()
PRODUCT_ID_A = uuid.uuid4()
PRODUCT_ID_B = uuid.uuid4()

def generate_token(user_id: uuid.UUID) -> str:
    payload = {"sub": str(user_id)}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)

@pytest.mark.asyncio
async def test_add_sku_increments_quantity_if_already_in_cart(client: AsyncClient, test_db):
    """
    happy: add_sku_increments_quantity_if_already_in_cart
    """
    token = generate_token(USER_ID)
    
    mock_sku_response = {
        "id": str(SKU_ID_A),
        "product_id": str(PRODUCT_ID_A),
        "name": "Red / XL",
        "price": 100000,
        "discount": 10000,
        "active_quantity": 50,
        "article": "SKU-RED-XL",
        "images": [],
        "created_at": "2026-06-04T00:00:00Z",
        "updated_at": "2026-06-04T00:00:00Z"
    }
    
    mock_product_response = {
        "id": str(PRODUCT_ID_A),
        "title": "Cool T-Shirt",
        "slug": "cool-t-shirt",
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

    with patch("src.modules.catalog.service.CatalogService.get_b2b_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.__aenter__.return_value = mock_client
        
        # Mock SKU GET
        mock_sku_resp = AsyncMock(spec=Response)
        mock_sku_resp.status_code = 200
        mock_sku_resp.json.return_value = mock_sku_response
        mock_sku_resp.raise_for_status.return_value = None
        mock_client.get.return_value = mock_sku_resp

        # Mock Products Batch POST
        mock_batch_resp = AsyncMock(spec=Response)
        mock_batch_resp.status_code = 200
        mock_batch_resp.json.return_value = [mock_product_response]
        mock_batch_resp.raise_for_status.return_value = None
        mock_client.post.return_value = mock_batch_resp

        # First add: 2 items
        resp1 = await client.post(
            "/api/v1/cart/items",
            json={"sku_id": str(SKU_ID_A), "quantity": 2},
            headers={"Authorization": f"Bearer {token}"}
        )
        assert resp1.status_code == 200
        data1 = resp1.json()
        assert data1["items_count"] == 2
        assert len(data1["items"]) == 1
        assert data1["items"][0]["quantity"] == 2

        # Second add: 3 items (total should be 5)
        resp2 = await client.post(
            "/api/v1/cart/items",
            json={"sku_id": str(SKU_ID_A), "quantity": 3},
            headers={"Authorization": f"Bearer {token}"}
        )
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert data2["items_count"] == 5
        assert data2["items"][0]["quantity"] == 5

        # Check DB
        stmt = select(CartItem).where(CartItem.user_id == USER_ID, CartItem.sku_id == SKU_ID_A)
        result = await test_db.execute(stmt)
        db_item = result.scalars().first()
        assert db_item is not None
        assert db_item.quantity == 5


@pytest.mark.asyncio
async def test_get_cart_enriched_with_b2b_data(client: AsyncClient, test_db):
    """
    happy: get_cart_enriched_with_b2b_data
    """
    # Insert items manually
    item_a = CartItem(user_id=USER_ID, sku_id=SKU_ID_A, quantity=3, unit_price_at_add=90000)
    item_b = CartItem(user_id=USER_ID, sku_id=SKU_ID_B, quantity=1, unit_price_at_add=50000)
    test_db.add_all([item_a, item_b])
    await test_db.commit()

    token = generate_token(USER_ID)
    
    mock_sku_a = {
        "id": str(SKU_ID_A),
        "product_id": str(PRODUCT_ID_A),
        "name": "Red",
        "price": 100000,
        "discount": 10000,
        "active_quantity": 10,
        "article": "SKU-A",
        "created_at": "2026-06-04T00:00:00Z",
        "updated_at": "2026-06-04T00:00:00Z"
    }
    mock_sku_b = {
        "id": str(SKU_ID_B),
        "product_id": str(PRODUCT_ID_B),
        "name": "Blue",
        "price": 60000,
        "discount": 5000,
        "active_quantity": 2,
        "article": "SKU-B",
        "created_at": "2026-06-04T00:00:00Z",
        "updated_at": "2026-06-04T00:00:00Z"
    }

    mock_product_a = {
        "id": str(PRODUCT_ID_A),
        "title": "Product A",
        "status": "MODERATED",
        "deleted": False,
        "skus": [mock_sku_a]
    }
    mock_product_b = {
        "id": str(PRODUCT_ID_B),
        "title": "Product B",
        "status": "MODERATED",
        "deleted": False,
        "skus": [mock_sku_b]
    }

    with patch("src.modules.catalog.service.CatalogService.get_b2b_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.__aenter__.return_value = mock_client

        # Mock GET for both SKUs
        async def mock_get(url, *args, **kwargs):
            mock_resp = AsyncMock(spec=Response)
            mock_resp.status_code = 200
            mock_resp.raise_for_status.return_value = None
            if str(SKU_ID_A) in url:
                mock_resp.json.return_value = mock_sku_a
            elif str(SKU_ID_B) in url:
                mock_resp.json.return_value = mock_sku_b
            else:
                mock_resp.status_code = 404
            return mock_resp

        mock_client.get.side_effect = mock_get

        # Mock POST batch products
        mock_batch_resp = AsyncMock(spec=Response)
        mock_batch_resp.status_code = 200
        mock_batch_resp.json.return_value = [mock_product_a, mock_product_b]
        mock_batch_resp.raise_for_status.return_value = None
        mock_client.post.return_value = mock_batch_resp

        # Request cart
        response = await client.get(
            "/api/v1/cart",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["items_count"] == 4
        # unit_price_a = 90000, quantity_a = 3 -> 270000
        # unit_price_b = 55000, quantity_b = 1 -> 55000
        # total subtotal = 325000
        assert data["subtotal"] == 325000
        assert data["is_valid"] is True
        
        items = {item["sku_id"]: item for item in data["items"]}
        assert items[str(SKU_ID_A)]["name"] == "Product A Red"
        assert items[str(SKU_ID_A)]["unit_price"] == 90000
        assert items[str(SKU_ID_B)]["name"] == "Product B Blue"
        assert items[str(SKU_ID_B)]["unit_price"] == 55000


@pytest.mark.asyncio
async def test_unavailable_sku_shown_with_reason(client: AsyncClient, test_db):
    """
    unhappy: unavailable_sku_shown_with_reason
    """
    # Item A is out of stock, Item B is blocked, Item C has deleted parent product
    SKU_ID_DELETED = uuid.uuid4()
    PRODUCT_ID_DELETED = uuid.uuid4()
    item_a = CartItem(user_id=USER_ID, sku_id=SKU_ID_A, quantity=3, unit_price_at_add=90000)
    item_b = CartItem(user_id=USER_ID, sku_id=SKU_ID_B, quantity=1, unit_price_at_add=50000)
    item_c = CartItem(user_id=USER_ID, sku_id=SKU_ID_DELETED, quantity=1, unit_price_at_add=20000)
    test_db.add_all([item_a, item_b, item_c])
    await test_db.commit()

    token = generate_token(USER_ID)

    mock_sku_a = {
        "id": str(SKU_ID_A),
        "product_id": str(PRODUCT_ID_A),
        "name": "Red",
        "price": 100000,
        "discount": 10000,
        "active_quantity": 0,  # OUT OF STOCK
        "created_at": "2026-06-04T00:00:00Z",
        "updated_at": "2026-06-04T00:00:00Z"
    }
    mock_sku_b = {
        "id": str(SKU_ID_B),
        "product_id": str(PRODUCT_ID_B),
        "name": "Blue",
        "price": 60000,
        "discount": 5000,
        "active_quantity": 5,
        "created_at": "2026-06-04T00:00:00Z",
        "updated_at": "2026-06-04T00:00:00Z"
    }
    mock_sku_c = {
        "id": str(SKU_ID_DELETED),
        "product_id": str(PRODUCT_ID_DELETED),
        "name": "Green",
        "price": 20000,
        "discount": 0,
        "active_quantity": 5,
        "created_at": "2026-06-04T00:00:00Z",
        "updated_at": "2026-06-04T00:00:00Z"
    }

    mock_product_a = {
        "id": str(PRODUCT_ID_A),
        "title": "Product A",
        "status": "MODERATED",
        "deleted": False,
        "skus": [mock_sku_a]
    }
    mock_product_b = {
        "id": str(PRODUCT_ID_B),
        "title": "Product B",
        "status": "BLOCKED",  # BLOCKED
        "deleted": False,
        "skus": [mock_sku_b]
    }
    mock_product_c = {
        "id": str(PRODUCT_ID_DELETED),
        "title": "Product C",
        "status": "MODERATED",
        "deleted": True,  # DELETED
        "skus": [mock_sku_c]
    }

    with patch("src.modules.catalog.service.CatalogService.get_b2b_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.__aenter__.return_value = mock_client

        async def mock_get(url, *args, **kwargs):
            mock_resp = AsyncMock(spec=Response)
            mock_resp.status_code = 200
            mock_resp.raise_for_status.return_value = None
            if str(SKU_ID_A) in url:
                mock_resp.json.return_value = mock_sku_a
            elif str(SKU_ID_B) in url:
                mock_resp.json.return_value = mock_sku_b
            elif str(SKU_ID_DELETED) in url:
                mock_resp.json.return_value = mock_sku_c
            elif f"/products/{PRODUCT_ID_B}" in url:
                mock_resp.json.return_value = mock_product_b
            elif f"/products/{PRODUCT_ID_A}" in url:
                mock_resp.json.return_value = mock_product_a
            elif f"/products/{PRODUCT_ID_DELETED}" in url:
                mock_resp.json.return_value = mock_product_c
            else:
                mock_resp.status_code = 404
                mock_resp.json.return_value = {"code": "NOT_FOUND"}
                mock_resp.raise_for_status.side_effect = Exception("Not Found")
            return mock_resp

        mock_client.get.side_effect = mock_get

        mock_batch_resp = AsyncMock(spec=Response)
        mock_batch_resp.status_code = 200
        # Batch only returns active/moderated/non-deleted products, so product B and C are absent
        mock_batch_resp.json.return_value = [mock_product_a]
        mock_batch_resp.raise_for_status.return_value = None
        mock_client.post.return_value = mock_batch_resp

        # Call Validate Cart
        response = await client.post(
            "/api/v1/cart/validate",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        data = response.json()
        
        assert data["is_valid"] is False
        issues = {issue["sku_id"]: issue for issue in data["issues"]}
        
        assert str(SKU_ID_A) in issues
        assert issues[str(SKU_ID_A)]["type"] == "OUT_OF_STOCK"

        assert str(SKU_ID_B) in issues
        assert issues[str(SKU_ID_B)]["type"] == "PRODUCT_BLOCKED"

        assert str(SKU_ID_DELETED) in issues
        assert issues[str(SKU_ID_DELETED)]["type"] == "PRODUCT_DELETED"


@pytest.mark.asyncio
async def test_guest_cart_merged_on_login(client: AsyncClient, test_db):
    """
    happy: guest_cart_merged_on_login
    """
    # SKU_A: guest has 2, user has 3 -> after merge, should be max(2, 3) = 3
    # SKU_B: guest has 4, user has 0 -> after merge, should be 4
    item_guest_a = CartItem(session_id=SESSION_ID, sku_id=SKU_ID_A, quantity=2, unit_price_at_add=1000)
    item_guest_b = CartItem(session_id=SESSION_ID, sku_id=SKU_ID_B, quantity=4, unit_price_at_add=2000)
    item_user_a = CartItem(user_id=USER_ID, sku_id=SKU_ID_A, quantity=3, unit_price_at_add=1000)
    
    test_db.add_all([item_guest_a, item_guest_b, item_user_a])
    await test_db.commit()

    token = generate_token(USER_ID)

    mock_sku_a = {"id": str(SKU_ID_A), "product_id": str(PRODUCT_ID_A), "price": 1000, "discount": 0, "active_quantity": 10, "name": "A"}
    mock_sku_b = {"id": str(SKU_ID_B), "product_id": str(PRODUCT_ID_B), "price": 2000, "discount": 0, "active_quantity": 10, "name": "B"}
    mock_product_a = {"id": str(PRODUCT_ID_A), "title": "Prod A", "status": "MODERATED", "deleted": False, "skus": [mock_sku_a]}
    mock_product_b = {"id": str(PRODUCT_ID_B), "title": "Prod B", "status": "MODERATED", "deleted": False, "skus": [mock_sku_b]}

    with patch("src.modules.catalog.service.CatalogService.get_b2b_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.__aenter__.return_value = mock_client

        async def mock_get(url, *args, **kwargs):
            mock_resp = AsyncMock(spec=Response)
            mock_resp.status_code = 200
            mock_resp.raise_for_status.return_value = None
            if str(SKU_ID_A) in url:
                mock_resp.json.return_value = mock_sku_a
            elif str(SKU_ID_B) in url:
                mock_resp.json.return_value = mock_sku_b
            return mock_resp

        mock_client.get.side_effect = mock_get

        mock_batch_resp = AsyncMock(spec=Response)
        mock_batch_resp.status_code = 200
        mock_batch_resp.json.return_value = [mock_product_a, mock_product_b]
        mock_batch_resp.raise_for_status.return_value = None
        mock_client.post.return_value = mock_batch_resp

        # Call Merge Cart endpoint
        response = await client.post(
            "/api/v1/cart/merge",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Session-Id": SESSION_ID
            }
        )
        assert response.status_code == 200
        data = response.json()
        assert data["items_count"] == 7  # 3 (SKU_A) + 4 (SKU_B)
        
        items = {item["sku_id"]: item for item in data["items"]}
        assert items[str(SKU_ID_A)]["quantity"] == 3
        assert items[str(SKU_ID_B)]["quantity"] == 4

        # Verify DB has no remaining session items
        stmt_session = select(CartItem).where(CartItem.session_id == SESSION_ID)
        result_session = await test_db.execute(stmt_session)
        assert len(result_session.scalars().all()) == 0

        # Verify DB has correct user items
        stmt_user = select(CartItem).where(CartItem.user_id == USER_ID)
        result_user = await test_db.execute(stmt_user)
        db_items = {item.sku_id: item.quantity for item in result_user.scalars().all()}
        assert db_items[SKU_ID_A] == 3
        assert db_items[SKU_ID_B] == 4


@pytest.mark.asyncio
async def test_get_cart_with_missing_sku_returns_200_unavailable(client: AsyncClient, test_db):
    """
    unhappy: test_get_cart_with_missing_sku_returns_200_unavailable
    """
    item_deleted = CartItem(user_id=USER_ID, sku_id=SKU_ID_A, quantity=3, unit_price_at_add=90000)
    test_db.add(item_deleted)
    await test_db.commit()

    token = generate_token(USER_ID)

    with patch("src.modules.catalog.service.CatalogService.get_b2b_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.__aenter__.return_value = mock_client

        # Mock GET SKU to return 404 Not Found
        mock_resp = AsyncMock(spec=Response)
        mock_resp.status_code = 404
        mock_resp.json.return_value = {"code": "NOT_FOUND"}
        mock_resp.raise_for_status.side_effect = Exception("Not Found")
        mock_client.get.return_value = mock_resp

        response = await client.get(
            "/api/v1/cart",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["is_valid"] is False
        assert data["items_count"] == 3
        assert data["subtotal"] == 0
        assert len(data["items"]) == 1
        item = data["items"][0]
        assert item["sku_id"] == str(SKU_ID_A)
        assert item["is_available"] is False
        assert item["unavailable_reason"] == "PRODUCT_DELETED"
        assert item["name"] == "Товар недоступен"


@pytest.mark.asyncio
async def test_get_cart_with_sku_missing_product_id_returns_200_unavailable(client: AsyncClient, test_db):
    """
    unhappy: test_get_cart_with_sku_missing_product_id_returns_200_unavailable
    """
    item_no_product = CartItem(user_id=USER_ID, sku_id=SKU_ID_A, quantity=3, unit_price_at_add=90000)
    test_db.add(item_no_product)
    await test_db.commit()

    token = generate_token(USER_ID)

    with patch("src.modules.catalog.service.CatalogService.get_b2b_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.__aenter__.return_value = mock_client

        # Mock GET SKU to return a dict WITHOUT product_id
        mock_sku_response = {
            "id": str(SKU_ID_A),
            "name": "Red / XL",
            "price": 100000,
            "discount": 10000,
            "active_quantity": 50,
            "article": "SKU-RED-XL",
            "images": []
        }
        mock_resp = AsyncMock(spec=Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_sku_response
        mock_resp.raise_for_status.return_value = None
        mock_client.get.return_value = mock_resp

        response = await client.get(
            "/api/v1/cart",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["is_valid"] is False
        assert data["items_count"] == 3
        assert data["subtotal"] == 0
        assert len(data["items"]) == 1
        item = data["items"][0]
        assert item["sku_id"] == str(SKU_ID_A)
        assert item["is_available"] is False
        assert item["unavailable_reason"] == "PRODUCT_DELETED"
        assert item["name"] == "Товар недоступен"


