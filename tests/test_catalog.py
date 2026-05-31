import pytest
from httpx import AsyncClient, RequestError, HTTPStatusError, Request, Response
from unittest.mock import patch, AsyncMock

CATEGORY_ID = "123e4567-e89b-42d3-a456-426614174001"
PRODUCT_ID = "770e8400-e29b-41d4-a716-446655440002"
BRAND_FILTER_ID = "f8a9e9a4-1234-4567-8901-abcdef123456"

@pytest.mark.asyncio
async def test_catalog_returns_filtered_sorted_products(client: AsyncClient):
    mock_b2b_response = {
        "items": [
            {
                "id": PRODUCT_ID,
                "title": "iPhone 15 Pro Max",
                "image": "https://cdn.neomarket.ru/images/iphone15.jpg",
                "price": 12999000,
                "in_stock": True,
                "is_in_cart": False
            }
        ],
        "total_count": 1,
        "limit": 20,
        "offset": 0
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

        # Request with filter and sort
        response = await client.get(f"/api/v1/catalog/products?filter[category_id]={CATEGORY_ID}&filter[attributes][brand]=Apple&sort=price_asc&limit=20&offset=0")
        
        assert response.status_code == 200
        data = response.json()
        assert data["items"][0]["title"] == "iPhone 15 Pro Max"
        
        # Verify translation
        called_args, called_kwargs = mock_client.get.call_args
        assert called_args[0] == "/api/v1/public/products"
        params = called_kwargs.get("params")
        assert ("category_id", CATEGORY_ID) in params
        assert ("filters[brand]", "Apple") in params
        assert ("sort", "price_asc") in params
        assert ("limit", "20") in params
        assert ("offset", "0") in params

@pytest.mark.asyncio
async def test_facets_return_counts_per_filter_value(client: AsyncClient):
    mock_facets_response = {
        "category_id": CATEGORY_ID,
        "facets": [
            {
                "name": "brand",
                "values": [
                    {"value": "Apple", "count": 124},
                    {"value": "Samsung", "count": 98}
                ]
            }
        ]
    }
    
    with patch("src.modules.catalog.service.CatalogService.get_b2b_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.__aenter__.return_value = mock_client
        
        mock_response = AsyncMock(spec=Response)
        mock_response.status_code = 200
        mock_response.json.return_value = mock_facets_response
        mock_response.raise_for_status.return_value = None
        mock_client.get.return_value = mock_response

        response = await client.get(f"/api/v1/catalog/facets?category_id={CATEGORY_ID}&filters[brand]=Apple")
        
        assert response.status_code == 200
        data = response.json()
        assert data["category_id"] == CATEGORY_ID
        assert len(data["facets"]) == 1
        assert data["facets"][0]["name"] == "brand"

@pytest.mark.asyncio
async def test_invalid_sort_returns_400(client: AsyncClient):
    response = await client.get("/api/v1/catalog/products?sort=invalid_sort")
    assert response.status_code == 400
    data = response.json()
    assert data["code"] == "INVALID_REQUEST"
    assert "Invalid sort parameter" in data["message"]
    assert "rating, popularity, price_asc, price_desc, date_desc, discount_desc" in data["message"]

@pytest.mark.asyncio
async def test_b2b_unavailable_returns_502(client: AsyncClient):
    with patch("src.modules.catalog.service.CatalogService.get_b2b_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.__aenter__.return_value = mock_client
        
        # Simulate B2B downtime
        mock_client.get.side_effect = RequestError("Failed to connect", request=Request("GET", "/api/v1/public/products"))
        
        response = await client.get("/api/v1/catalog/products?sort=popularity")
        
        assert response.status_code == 502
        data = response.json()
        assert data["code"] == "BAD_GATEWAY"
        assert "unavailable" in data["message"].lower()

@pytest.mark.asyncio
async def test_category_filters_returns_expected_format(client: AsyncClient):
    mock_filters_response = [
        {
            "id": BRAND_FILTER_ID,
            "name": "brand",
            "title": "Brand",
            "type": "checkbox",
            "options": [
                {"value": "Apple", "title": "Apple"}
            ]
        }
    ]
    
    with patch("src.modules.catalog.service.CatalogService.get_b2b_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.__aenter__.return_value = mock_client
        
        mock_response = AsyncMock(spec=Response)
        mock_response.status_code = 200
        mock_response.json.return_value = mock_filters_response
        mock_response.raise_for_status.return_value = None
        mock_client.get.return_value = mock_response

        response = await client.get(f"/api/v1/catalog/categories/{CATEGORY_ID}/filters")
        
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["name"] == "brand"
        
        # Verify translation
        called_args, _ = mock_client.get.call_args
        assert called_args[0] == f"/api/v1/public/categories/{CATEGORY_ID}/filters"

@pytest.mark.asyncio
async def test_search_returns_matching_products(client: AsyncClient):
    mock_b2b_response = {
        "items": [
            {
                "id": PRODUCT_ID,
                "title": "Bluetooth Headphones",
                "image": "url",
                "price": 500000,
                "in_stock": True,
                "is_in_cart": False
            }
        ],
        "total_count": 1,
        "limit": 20,
        "offset": 0
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

        response = await client.get("/api/v1/catalog/products?q=Headphones")
        
        assert response.status_code == 200
        data = response.json()
        assert data["items"][0]["title"] == "Bluetooth Headphones"
        
        called_args, called_kwargs = mock_client.get.call_args
        params = called_kwargs.get("params")
        assert ("search", "Headphones") in params

@pytest.mark.asyncio
async def test_short_query_returns_400(client: AsyncClient):
    response = await client.get("/api/v1/catalog/products?q=ab")
    assert response.status_code == 400
    data = response.json()
    assert data["code"] == "INVALID_REQUEST"
    assert "at least 3 characters" in data["message"]

@pytest.mark.asyncio
async def test_special_chars_do_not_break_query(client: AsyncClient):
    mock_b2b_response = {"items": [], "total_count": 0, "limit": 20, "offset": 0}
    with patch("src.modules.catalog.service.CatalogService.get_b2b_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.__aenter__.return_value = mock_client
        
        mock_response = AsyncMock(spec=Response)
        mock_response.status_code = 200
        mock_response.json.return_value = mock_b2b_response
        mock_response.raise_for_status.return_value = None
        mock_client.get.return_value = mock_response

        response = await client.get("/api/v1/catalog/products?q=100%25_real")
        
        assert response.status_code == 200
        called_args, called_kwargs = mock_client.get.call_args
        params = called_kwargs.get("params")
        assert ("search", "100%_real") in params

@pytest.mark.asyncio
async def test_empty_results_returns_200(client: AsyncClient):
    mock_b2b_response = {"items": [], "total_count": 0, "limit": 20, "offset": 0}
    with patch("src.modules.catalog.service.CatalogService.get_b2b_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.__aenter__.return_value = mock_client
        
        mock_response = AsyncMock(spec=Response)
        mock_response.status_code = 200
        mock_response.json.return_value = mock_b2b_response
        mock_response.raise_for_status.return_value = None
        mock_client.get.return_value = mock_response

        response = await client.get("/api/v1/catalog/products?q=NotExistingProduct")
        
        assert response.status_code == 200
        data = response.json()
        assert data["items"] == []
        assert data["total_count"] == 0
