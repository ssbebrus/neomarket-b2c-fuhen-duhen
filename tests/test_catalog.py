import pytest
from httpx import AsyncClient, RequestError, HTTPStatusError, Request, Response
from unittest.mock import patch, AsyncMock
import uuid

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
                "cover_image": "https://cdn.neomarket.ru/images/iphone15.jpg",
                "min_price": 12999000,
                "status": "MODERATED",
                "category_id": CATEGORY_ID,
                "slug": "iphone-15-pro-max",
                "created_at": "2026-06-03T11:16:15.057Z"
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
        assert data["items"][0]["name"] == "iPhone 15 Pro Max"
        
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
    assert "price_asc, price_desc, popularity, new" in data["message"]

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
                "cover_image": "url",
                "min_price": 500000,
                "status": "MODERATED",
                "category_id": CATEGORY_ID,
                "slug": "bluetooth-headphones",
                "created_at": "2026-06-03T11:16:15.057Z"
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
        assert data["items"][0]["name"] == "Bluetooth Headphones"
        
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

@pytest.mark.asyncio
async def test_product_card_returns_full_data_with_skus(client: AsyncClient):
    mock_b2b_response = {
        "id": PRODUCT_ID,
        "seller_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
        "category_id": CATEGORY_ID,
        "category": {
            "id": CATEGORY_ID,
            "name": "Electronics",
            "level": 0,
            "path": f"3fa85f64-5717-4562-b3fc-2c963f66afa6.{CATEGORY_ID}"
        },
        "seller": {
            "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
            "display_name": "Apple Store"
        },
        "title": "iPhone 15 Pro Max",
        "slug": "iphone-15-pro-max",
        "description": "Флагманский смартфон",
        "status": "MODERATED",
        "images": [
            {
                "url": "https://cdn.neomarket.ru/images/iphone15.jpg",
                "ordering": 0,
                "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6"
            }
        ],
        "characteristics": [
            {
                "name": "Brand",
                "value": "Apple",
                "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6"
            }
        ],
        "skus": [
            {
                "id": "660e8400-e29b-41d4-a716-446655440001",
                "product_id": PRODUCT_ID,
                "name": "256GB Black",
                "price": 12999000,
                "discount": 500000,
                "stock_quantity": 15,
                "active_quantity": 10,
                "article": "IP15PM-256-BLK",
                "images": [],
                "characteristics": []
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

        response = await client.get(f"/api/v1/catalog/products/{PRODUCT_ID}")
        
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == PRODUCT_ID
        assert data["seller"]["id"] == "3fa85f64-5717-4562-b3fc-2c963f66afa6"
        assert data["seller"]["display_name"] == "Apple Store"
        assert data["category"]["name"] == "Electronics"
        assert data["category"]["path"] == ["3fa85f64-5717-4562-b3fc-2c963f66afa6", CATEGORY_ID]
        assert str(data["category"]["parent_id"]) == "3fa85f64-5717-4562-b3fc-2c963f66afa6"
        assert data["name"] == "iPhone 15 Pro Max"
        assert data["slug"] == "iphone-15-pro-max"
        assert data["description"] == "Флагманский смартфон"
        assert len(data["images"]) == 1
        assert data["images"][0]["url"] == "https://cdn.neomarket.ru/images/iphone15.jpg"
        assert data["attributes"] == {"Brand": "Apple"}
        
        assert data["has_stock"] is True
        assert data["min_price"] == 12499000
        assert data["old_price"] == 12999000
        
        assert len(data["skus"]) == 1
        sku = data["skus"][0]
        assert sku["id"] == "660e8400-e29b-41d4-a716-446655440001"
        assert sku["name"] == "256GB Black"
        assert sku["price"] == 12499000
        assert sku["old_price"] == 12999000
        assert sku["available_quantity"] == 10
        assert sku["sku_code"] == "IP15PM-256-BLK"
        assert sku["images"] == []
        assert sku["attributes"] == {}
        
        called_args, _ = mock_client.get.call_args
        assert called_args[0] == f"/api/v1/public/products/{PRODUCT_ID}"

@pytest.mark.asyncio
async def test_product_card_returns_has_stock_false_if_no_active_quantity(client: AsyncClient):
    mock_b2b_response = {
        "id": PRODUCT_ID,
        "seller_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
        "category_id": CATEGORY_ID,
        "category": {
            "id": CATEGORY_ID,
            "name": "Electronics",
            "level": 0,
            "path": f"3fa85f64-5717-4562-b3fc-2c963f66afa6.{CATEGORY_ID}"
        },
        "seller": {
            "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
            "display_name": "Apple Store"
        },
        "title": "iPhone 15 Pro Max",
        "slug": "iphone-15-pro-max",
        "description": "Флагманский смартфон",
        "skus": [
            {
                "id": "660e8400-e29b-41d4-a716-446655440001",
                "product_id": PRODUCT_ID,
                "name": "256GB Black",
                "price": 12999000,
                "active_quantity": 0
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

        response = await client.get(f"/api/v1/catalog/products/{PRODUCT_ID}")
        
        assert response.status_code == 200
        data = response.json()
        assert data["has_stock"] is False


@pytest.mark.asyncio
async def test_cost_price_absent_in_response(client: AsyncClient):
    # Simulate B2B returning cost_price and reserved_quantity
    mock_b2b_response = {
        "id": PRODUCT_ID,
        "seller_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
        "category_id": CATEGORY_ID,
        "category": {
            "id": CATEGORY_ID,
            "name": "Electronics",
            "level": 0,
            "path": f"3fa85f64-5717-4562-b3fc-2c963f66afa6.{CATEGORY_ID}"
        },
        "seller": {
            "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
            "display_name": "Apple Store"
        },
        "title": "iPhone 15 Pro Max",
        "slug": "iphone-15-pro-max",
        "description": "Флагманский смартфон",
        "skus": [
            {
                "id": "660e8400-e29b-41d4-a716-446655440001",
                "product_id": PRODUCT_ID,
                "name": "256GB Black",
                "price": 12999000,
                "cost_price": 10000000,
                "reserved_quantity": 2,
                "active_quantity": 10
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

        response = await client.get(f"/api/v1/catalog/products/{PRODUCT_ID}")
        
        assert response.status_code == 200
        data = response.json()
        
        # Explicit test for requirement: assert 'cost_price' not in response.json()['skus'][0]
        assert "cost_price" not in data["skus"][0]
        assert "reserved_quantity" not in data["skus"][0]
        assert data["skus"][0]["price"] == 12999000

@pytest.mark.asyncio
async def test_blocked_product_returns_404(client: AsyncClient):
    with patch("src.modules.catalog.service.CatalogService.get_b2b_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.__aenter__.return_value = mock_client
        
        # Simulate B2B returning 404 for a blocked/deleted product
        mock_response = AsyncMock(spec=Response)
        mock_response.status_code = 404
        mock_response.json.return_value = {"code": "NOT_FOUND", "message": "Product not found"}
        
        mock_client.get.side_effect = HTTPStatusError(
            "404 Not Found",
            request=Request("GET", f"/api/v1/public/products/{PRODUCT_ID}"),
            response=mock_response
        )

        response = await client.get(f"/api/v1/catalog/products/{PRODUCT_ID}")
        
        assert response.status_code == 404
        data = response.json()
        assert data["code"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_empty_category_returns_200_empty_list(client: AsyncClient):
    with patch("src.modules.catalog.service.CatalogService.get_b2b_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.__aenter__.return_value = mock_client
        
        mock_response = AsyncMock(spec=Response)
        mock_response.status_code = 200
        mock_response.json.return_value = []
        mock_response.raise_for_status.return_value = None
        mock_client.get.return_value = mock_response

        response = await client.get(f"/api/v1/catalog/products/{PRODUCT_ID}/similar?limit=10")
        
        assert response.status_code == 200
        data = response.json()
        assert data == []

@pytest.mark.asyncio
async def test_unknown_product_returns_404(client: AsyncClient):
    with patch("src.modules.catalog.service.CatalogService.get_b2b_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.__aenter__.return_value = mock_client
        
        mock_response = AsyncMock(spec=Response)
        mock_response.status_code = 404
        mock_response.json.return_value = {"code": "NOT_FOUND", "message": "Product not found"}
        
        mock_client.get.side_effect = HTTPStatusError(
            "404 Not Found",
            request=Request("GET", f"/api/v1/public/products/{PRODUCT_ID}/similar"),
            response=mock_response
        )

        response = await client.get(f"/api/v1/catalog/products/{PRODUCT_ID}/similar")
        
        assert response.status_code == 404
        data = response.json()
        assert data["code"] == "NOT_FOUND"

@pytest.mark.asyncio
async def test_similar_returns_up_to_10_from_same_category(client: AsyncClient):
    import uuid
    mock_b2b_products = []
    for i in range(12):
        pid = str(uuid.uuid4())
        mock_b2b_products.append({
            "id": pid,
            "seller_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
            "category_id": CATEGORY_ID,
            "category": {
                "id": CATEGORY_ID,
                "name": "Electronics",
                "level": 0,
                "path": f"3fa85f64-5717-4562-b3fc-2c963f66afa6.{CATEGORY_ID}"
            },
            "title": f"Similar Product {i}",
            "slug": f"similar-product-{i}",
            "description": "...",
            "status": "MODERATED",
            "images": [{"url": "url", "ordering": 0, "id": str(uuid.uuid4())}],
            "characteristics": [],
            "skus": [
                {
                    "id": str(uuid.uuid4()),
                    "product_id": pid,
                    "name": "SKU",
                    "price": 1000,
                    "discount": 0,
                    "stock_quantity": 10,
                    "active_quantity": 10,
                    "article": f"SKU-{i}",
                    "images": [],
                    "characteristics": []
                }
            ]
        })
    
    mock_b2b_short_products = []
    for p in mock_b2b_products:
        mock_b2b_short_products.append({
            "id": p["id"],
            "title": p["title"],
            "slug": p["slug"],
            "status": p["status"],
            "category_id": p["category_id"],
            "min_price": 1000,
            "cover_image": "url",
            "created_at": "2026-06-03T11:16:15.057Z"
        })

    with patch("src.modules.catalog.service.CatalogService.get_b2b_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.__aenter__.return_value = mock_client
        
        async def mock_b2b_get(url, params=None, **kwargs):
            limit = int(params.get("limit", 10)) if params else 10
            from httpx import Response
            response = AsyncMock(spec=Response)
            response.status_code = 200
            response.json.return_value = mock_b2b_short_products[:limit]
            response.raise_for_status.return_value = None
            return response

        async def mock_b2b_post(url, json=None, **kwargs):
            from httpx import Response
            product_ids = json.get("product_ids", [])
            response = AsyncMock(spec=Response)
            response.status_code = 200
            response.json.return_value = [p for p in mock_b2b_products if p["id"] in product_ids]
            response.raise_for_status.return_value = None
            return response

        mock_client.get.side_effect = mock_b2b_get
        mock_client.post.side_effect = mock_b2b_post

        response = await client.get(f"/api/v1/catalog/products/{PRODUCT_ID}/similar?limit=10")
        
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 10
        assert data[0]["name"] == "Similar Product 0"
        assert PRODUCT_ID not in [p["id"] for p in data]
        
        called_args, called_kwargs = mock_client.get.call_args
        assert called_args[0] == f"/api/v1/public/products/{PRODUCT_ID}/similar"
        assert called_kwargs.get("params") == {"limit": 10}

        post_called_args, post_called_kwargs = mock_client.post.call_args
        assert post_called_args[0] == "/api/v1/public/products/batch"
        assert "product_ids" in post_called_kwargs.get("json", {})


@pytest.mark.asyncio
async def test_category_tree_returns_nested_structure(client: AsyncClient):
    mock_b2b_categories = [
        {
            "id": "123e4567-e89b-42d3-a456-426614174002",
            "name": "Электроника",
            "path": "123e4567-e89b-42d3-a456-426614174002",
            "level": 0,
            "is_active": True,
            "created_at": "2024-01-15T10:30:00Z",
            "updated_at": "2024-03-01T14:20:00Z"
        },
        {
            "id": "123e4567-e89b-42d3-a456-426614174003",
            "name": "Смартфоны",
            "path": "123e4567-e89b-42d3-a456-426614174002.123e4567-e89b-42d3-a456-426614174003",
            "level": 1,
            "is_active": True,
            "created_at": "2024-01-15T10:30:00Z",
            "updated_at": "2024-03-01T14:20:00Z"
        },
        {
            "id": "123e4567-e89b-42d3-a456-426614174004",
            "name": "Android",
            "path": "123e4567-e89b-42d3-a456-426614174002.123e4567-e89b-42d3-a456-426614174003.123e4567-e89b-42d3-a456-426614174004",
            "level": 2,
            "is_active": True,
            "created_at": "2024-01-15T10:30:00Z",
            "updated_at": "2024-03-01T14:20:00Z"
        }
    ]
    with patch("src.modules.catalog.service.CatalogService.get_b2b_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.__aenter__.return_value = mock_client
        
        mock_response = AsyncMock(spec=Response)
        mock_response.status_code = 200
        mock_response.json.return_value = mock_b2b_categories
        mock_response.raise_for_status.return_value = None
        mock_client.get.return_value = mock_response

        response = await client.get("/api/v1/catalog/categories/tree")
        
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["name"] == "Электроника"
        assert len(data[0]["children"]) == 1
        assert data[0]["children"][0]["name"] == "Смартфоны"
        assert len(data[0]["children"][0]["children"]) == 1
        assert data[0]["children"][0]["children"][0]["name"] == "Android"


@pytest.mark.asyncio
async def test_category_details_success(client: AsyncClient):
    mock_b2b_categories = [
        {
            "id": "123e4567-e89b-42d3-a456-426614174002",
            "name": "Электроника",
            "path": "123e4567-e89b-42d3-a456-426614174002",
            "level": 0,
            "is_active": True
        },
        {
            "id": "123e4567-e89b-42d3-a456-426614174003",
            "name": "Смартфоны",
            "path": "123e4567-e89b-42d3-a456-426614174002.123e4567-e89b-42d3-a456-426614174003",
            "level": 1,
            "is_active": True
        }
    ]
    with patch("src.modules.catalog.service.CatalogService.get_b2b_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.__aenter__.return_value = mock_client
        
        mock_response = AsyncMock(spec=Response)
        mock_response.status_code = 200
        mock_response.json.return_value = mock_b2b_categories
        mock_response.raise_for_status.return_value = None
        mock_client.get.return_value = mock_response

        response = await client.get("/api/v1/catalog/categories/123e4567-e89b-42d3-a456-426614174003")
        
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "123e4567-e89b-42d3-a456-426614174003"
        assert data["name"] == "Смартфоны"
        assert data["slug"] == "smartfony"
        assert data["parent"]["id"] == "123e4567-e89b-42d3-a456-426614174002"
        assert data["parent"]["name"] == "Электроника"
        assert data["parent"]["slug"] == "elektronika"
        assert "seo" in data
        assert data["seo"]["title"] == "Купить смартфоны в интернет-магазине | NeoMarket"


@pytest.mark.asyncio
async def test_breadcrumbs_return_path_from_root(client: AsyncClient):
    mock_b2b_categories = [
        {
            "id": "123e4567-e89b-42d3-a456-426614174002",
            "name": "Электроника",
            "path": "123e4567-e89b-42d3-a456-426614174002",
            "level": 0,
            "is_active": True
        },
        {
            "id": "123e4567-e89b-42d3-a456-426614174003",
            "name": "Смартфоны",
            "path": "123e4567-e89b-42d3-a456-426614174002.123e4567-e89b-42d3-a456-426614174003",
            "level": 1,
            "is_active": True
        }
    ]
    with patch("src.modules.catalog.service.CatalogService.get_b2b_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.__aenter__.return_value = mock_client
        
        mock_response = AsyncMock(spec=Response)
        mock_response.status_code = 200
        mock_response.json.return_value = mock_b2b_categories
        mock_response.raise_for_status.return_value = None
        mock_client.get.return_value = mock_response

        # Test resolution via category_id
        response = await client.get("/api/v1/catalog/breadcrumbs?category_id=123e4567-e89b-42d3-a456-426614174003")
        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]) == 2
        assert data["data"][0]["name"] == "Электроника"
        assert data["data"][0]["url"] == "/catalog/elektronika"
        assert data["data"][1]["name"] == "Смартфоны"
        assert data["data"][1]["url"] == "/catalog/elektronika/smartfony"
        assert data["data"][1]["is_current"] is True
        assert data["meta"]["resolved_via"] == "category_id"

        # Test resolution via product_id
        mock_product_response = {
            "id": "770e8400-e29b-41d4-a716-446655440002",
            "category": {
                "id": "123e4567-e89b-42d3-a456-426614174003",
                "name": "Смартфоны"
            }
        }
        
        async def mock_get(url, params=None, **kwargs):
            res = AsyncMock(spec=Response)
            res.status_code = 200
            res.raise_for_status.return_value = None
            if "products/770e8400-e29b-41d4-a716-446655440002" in url:
                res.json.return_value = mock_product_response
            else:
                res.json.return_value = mock_b2b_categories
            return res
            
        mock_client.get.side_effect = mock_get
        
        response2 = await client.get("/api/v1/catalog/breadcrumbs?product_id=770e8400-e29b-41d4-a716-446655440002")
        assert response2.status_code == 200
        data2 = response2.json()
        assert len(data2["data"]) == 2
        assert data2["data"][1]["name"] == "Смартфоны"
        assert data2["meta"]["resolved_via"] == "product_id"


@pytest.mark.asyncio
async def test_ambiguous_params_returns_400(client: AsyncClient):
    # Both provided
    response = await client.get("/api/v1/catalog/breadcrumbs?category_id=123e4567-e89b-42d3-a456-426614174003&product_id=770e8400-e29b-41d4-a716-446655440002")
    assert response.status_code == 400
    data = response.json()
    assert data["code"] == "AMBIGUOUS_PARAM"
    
    # None provided
    response2 = await client.get("/api/v1/catalog/breadcrumbs")
    assert response2.status_code == 400
    data2 = response2.json()
    assert data2["code"] == "MISSING_PARAM"


@pytest.mark.asyncio
async def test_orphan_node_returns_422(client: AsyncClient):
    # Missing parent category: "123e4567-e89b-42d3-a456-426614174002" is missing from list
    mock_b2b_categories = [
        {
            "id": "123e4567-e89b-42d3-a456-426614174003",
            "name": "Смартфоны",
            "path": "123e4567-e89b-42d3-a456-426614174002.123e4567-e89b-42d3-a456-426614174003",
            "level": 1,
            "is_active": True
        }
    ]
    with patch("src.modules.catalog.service.CatalogService.get_b2b_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.__aenter__.return_value = mock_client
        
        mock_response = AsyncMock(spec=Response)
        mock_response.status_code = 200
        mock_response.json.return_value = mock_b2b_categories
        mock_response.raise_for_status.return_value = None
        mock_client.get.return_value = mock_response

        # Tree returns 422
        response_tree = await client.get("/api/v1/catalog/categories/tree")
        assert response_tree.status_code == 422
        assert response_tree.json()["code"] == "ORPHAN_NODE"

        # Details returns 422
        response_detail = await client.get("/api/v1/catalog/categories/123e4567-e89b-42d3-a456-426614174003")
        assert response_detail.status_code == 422
        assert response_detail.json()["code"] == "ORPHAN_NODE"

        # Breadcrumbs returns 422
        response_crumbs = await client.get("/api/v1/catalog/breadcrumbs?category_id=123e4567-e89b-42d3-a456-426614174003")
        assert response_crumbs.status_code == 422
        assert response_crumbs.json()["code"] == "ORPHAN_NODE"


@pytest.mark.asyncio
async def test_unknown_category_returns_404(client: AsyncClient):
    mock_b2b_categories = []
    with patch("src.modules.catalog.service.CatalogService.get_b2b_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.__aenter__.return_value = mock_client
        
        mock_response = AsyncMock(spec=Response)
        mock_response.status_code = 200
        mock_response.json.return_value = mock_b2b_categories
        mock_response.raise_for_status.return_value = None
        mock_client.get.return_value = mock_response

        response = await client.get("/api/v1/catalog/categories/123e4567-e89b-42d3-a456-426614174009")
        assert response.status_code == 404
        assert response.json()["code"] == "NOT_FOUND"


