import pytest
import uuid
import jwt
from httpx import AsyncClient, Response, HTTPStatusError, Request
from unittest.mock import patch, AsyncMock
from sqlalchemy import select

from src.config import settings
from src.modules.favorites.models import Favorite

# Mock IDs
USER_ID_A = uuid.uuid4()
USER_ID_B = uuid.uuid4()
PRODUCT_ID = uuid.uuid4()
BLOCKED_PRODUCT_ID = uuid.uuid4()

def generate_token(user_id: uuid.UUID) -> str:
    payload = {"sub": str(user_id)}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)

@pytest.mark.asyncio
async def test_add_to_favorites_returns_201(client: AsyncClient, test_db):
    """
    happy: add_to_favorites_returns_201
    """
    token = generate_token(USER_ID_A)
    mock_b2b_response = {
        "id": str(PRODUCT_ID),
        "title": "iPhone 15 Pro Max",
        "slug": "iphone-15-pro-max",
        "category_id": str(uuid.uuid4()),
        "seller_id": str(uuid.uuid4()),
        "images": [],
        "characteristics": [],
        "skus": [
            {
                "id": str(uuid.uuid4()),
                "name": "256GB Black",
                "price": 12999000,
                "active_quantity": 10,
                "article": "IP15PM-BLK"
            }
        ]
    }

    with patch("src.modules.catalog.service.CatalogService.get_b2b_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.__aenter__.return_value = mock_client
        
        mock_response = AsyncMock(spec=Response)
        mock_response.status_code = 200
        mock_response.json.return_value = mock_b2b_response
        mock_response.raise_for_status.return_value = None
        mock_client.get.return_value = mock_response

        # Request to add to favorites
        response = await client.post(
            f"/api/v1/favorites/{PRODUCT_ID}",
            headers={"Authorization": f"Bearer {token}"}
        )
        
        assert response.status_code == 201
        data = response.json()
        assert data["product_id"] == str(PRODUCT_ID)
        assert data["user_id"] == str(USER_ID_A)
        assert "added_at" in data

        # Verify DB entry
        stmt = select(Favorite).where(Favorite.user_id == USER_ID_A, Favorite.product_id == PRODUCT_ID)
        result = await test_db.execute(stmt)
        entry = result.scalars().first()
        assert entry is not None

@pytest.mark.asyncio
async def test_repeat_add_returns_200_not_duplicate(client: AsyncClient, test_db):
    """
    unhappy: repeat_add_returns_200_not_duplicate (should be 200, not 409 or duplicate in DB)
    """
    token = generate_token(USER_ID_A)
    mock_b2b_response = {
        "id": str(PRODUCT_ID),
        "title": "iPhone 15 Pro Max",
        "slug": "iphone-15-pro-max",
        "category_id": str(uuid.uuid4()),
        "seller_id": str(uuid.uuid4()),
        "images": [],
        "characteristics": [],
        "skus": [{"id": str(uuid.uuid4()), "name": "256GB Black", "price": 12999000, "active_quantity": 10}]
    }

    with patch("src.modules.catalog.service.CatalogService.get_b2b_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.__aenter__.return_value = mock_client
        
        mock_response = AsyncMock(spec=Response)
        mock_response.status_code = 200
        mock_response.json.return_value = mock_b2b_response
        mock_response.raise_for_status.return_value = None
        mock_client.get.return_value = mock_response

        # Add first time (201)
        response1 = await client.post(
            f"/api/v1/favorites/{PRODUCT_ID}",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response1.status_code == 201

        # Add second time (200)
        response2 = await client.post(
            f"/api/v1/favorites/{PRODUCT_ID}",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response2.status_code == 200
        data2 = response2.json()
        assert data2["product_id"] == str(PRODUCT_ID)

        # Verify only one entry exists in database
        stmt = select(Favorite).where(Favorite.user_id == USER_ID_A, Favorite.product_id == PRODUCT_ID)
        result = await test_db.execute(stmt)
        entries = result.scalars().all()
        assert len(entries) == 1

@pytest.mark.asyncio
async def test_blocked_product_excluded_from_list(client: AsyncClient, test_db):
    """
    unhappy: blocked_product_excluded_from_list (GET excludes blocked/deleted in B2B products)
    """
    token = generate_token(USER_ID_A)
    
    # Insert two items into favorites DB manually to skip POST check for testing
    fav1 = Favorite(user_id=USER_ID_A, product_id=PRODUCT_ID)
    fav2 = Favorite(user_id=USER_ID_A, product_id=BLOCKED_PRODUCT_ID)
    test_db.add_all([fav1, fav2])
    await test_db.commit()

    # B2B batch response only contains one product (meaning the blocked one was excluded)
    mock_b2b_batch_response = [
        {
            "id": str(PRODUCT_ID),
            "title": "iPhone 15 Pro Max",
            "slug": "iphone-15-pro-max",
            "category_id": str(uuid.uuid4()),
            "category": {"id": str(uuid.uuid4()), "name": "Electronics", "level": 1, "path": "root.category"},
            "seller_id": str(uuid.uuid4()),
            "seller": {"id": str(uuid.uuid4()), "display_name": "Apple"},
            "images": [],
            "characteristics": [],
            "skus": [{"id": str(uuid.uuid4()), "name": "256GB Black", "price": 12999000, "active_quantity": 10}]
        }
    ]

    with patch("src.modules.catalog.service.CatalogService.get_b2b_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.__aenter__.return_value = mock_client
        
        mock_response = AsyncMock(spec=Response)
        mock_response.status_code = 200
        mock_response.json.return_value = mock_b2b_batch_response
        mock_response.raise_for_status.return_value = None
        mock_client.post.return_value = mock_response

        # Request to view favorites
        response = await client.get(
            "/api/v1/favorites",
            headers={"Authorization": f"Bearer {token}"}
        )
        
        assert response.status_code == 200
        data = response.json()
        
        # Should only contain 1 item (the active one)
        assert data["total_count"] == 1
        assert len(data["items"]) == 1
        assert data["items"][0]["id"] == str(PRODUCT_ID)
        
        # Verify B2B was queried with both IDs
        called_args, called_kwargs = mock_client.post.call_args
        assert called_args[0] == "/api/v1/public/products/batch"
        assert set(called_kwargs.get("json")["product_ids"]) == {str(PRODUCT_ID), str(BLOCKED_PRODUCT_ID)}

@pytest.mark.asyncio
async def test_user_id_from_query_is_ignored(client: AsyncClient, test_db):
    """
    user_id_from_query_is_ignored (if passed user_id in query, ignore it and use JWT claim)
    """
    token = generate_token(USER_ID_A)
    mock_b2b_response = {
        "id": str(PRODUCT_ID),
        "title": "iPhone 15 Pro Max",
        "slug": "iphone-15-pro-max",
        "category_id": str(uuid.uuid4()),
        "seller_id": str(uuid.uuid4()),
        "images": [],
        "characteristics": [],
        "skus": [{"id": str(uuid.uuid4()), "name": "256GB Black", "price": 12999000, "active_quantity": 10}]
    }

    with patch("src.modules.catalog.service.CatalogService.get_b2b_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.__aenter__.return_value = mock_client
        
        mock_response = AsyncMock(spec=Response)
        mock_response.status_code = 200
        mock_response.json.return_value = mock_b2b_response
        mock_response.raise_for_status.return_value = None
        mock_client.get.return_value = mock_response

        # Call POST with user_id in query targeting USER_ID_B
        response = await client.post(
            f"/api/v1/favorites/{PRODUCT_ID}?user_id={USER_ID_B}",
            headers={"Authorization": f"Bearer {token}"}
        )
        
        assert response.status_code == 201
        data = response.json()
        
        # Verify the response and database contains USER_ID_A, NOT USER_ID_B
        assert data["user_id"] == str(USER_ID_A)
        assert data["user_id"] != str(USER_ID_B)
        
        stmt = select(Favorite).where(Favorite.user_id == USER_ID_A, Favorite.product_id == PRODUCT_ID)
        result = await test_db.execute(stmt)
        assert result.scalars().first() is not None

        stmt_b = select(Favorite).where(Favorite.user_id == USER_ID_B, Favorite.product_id == PRODUCT_ID)
        result_b = await test_db.execute(stmt_b)
        assert result_b.scalars().first() is None

@pytest.mark.asyncio
async def test_delete_favorite_is_idempotent(client: AsyncClient, test_db):
    """
    DELETE is idempotent: removing existing or non-existing returns 204
    """
    token = generate_token(USER_ID_A)
    
    # 1. Add item to DB manually
    fav = Favorite(user_id=USER_ID_A, product_id=PRODUCT_ID)
    test_db.add(fav)
    await test_db.commit()

    # 2. Delete first time -> should return 204
    resp1 = await client.delete(
        f"/api/v1/favorites/{PRODUCT_ID}",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert resp1.status_code == 204

    # Verify deleted from DB
    stmt = select(Favorite).where(Favorite.user_id == USER_ID_A, Favorite.product_id == PRODUCT_ID)
    result = await test_db.execute(stmt)
    assert result.scalars().first() is None

    # 3. Delete second time -> should return 204
    resp2 = await client.delete(
        f"/api/v1/favorites/{PRODUCT_ID}",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert resp2.status_code == 204

@pytest.mark.asyncio
async def test_b2b_unavailable_returns_503(client: AsyncClient):
    """
    B2B is down -> returns 503
    """
    token = generate_token(USER_ID_A)

    with patch("src.modules.catalog.service.CatalogService.get_b2b_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.__aenter__.return_value = mock_client
        
        # Simulate B2B downtime during validation check
        mock_client.get.side_effect = TimeoutError("Connection timed out")

        response = await client.post(
            f"/api/v1/favorites/{PRODUCT_ID}",
            headers={"Authorization": f"Bearer {token}"}
        )
        
        assert response.status_code == 503
        data = response.json()
        assert data["code"] == "B2B_UNAVAILABLE"
