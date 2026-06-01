import httpx
import re
from fastapi import Request, HTTPException
from src.config import settings

ALLOWED_SORTS = {"price_asc", "price_desc", "popularity", "new"}

class CatalogService:
    @staticmethod
    def parse_filters(request: Request) -> dict:
        """
        Parses deepObject filters from query parameters.
        Example: 
        filter[category_id]=123 -> {'category_id': '123'}
        filter[attributes][brand]=Apple -> {'attributes': {'brand': 'Apple'}}
        """
        parsed = {}
        for key, value in request.query_params.multi_items():
            if key.startswith("filter["):
                # extract parts: filter[attributes][brand] -> ['attributes', 'brand']
                parts = re.findall(r'\[(.*?)\]', key)
                if not parts:
                    continue
                
                current = parsed
                for i, part in enumerate(parts):
                    if i == len(parts) - 1:
                        if part in current:
                            if isinstance(current[part], list):
                                current[part].append(value)
                            else:
                                current[part] = [current[part], value]
                        else:
                            current[part] = value
                    else:
                        if part not in current:
                            current[part] = {}
                        current = current[part]
        return parsed

    @staticmethod
    async def get_b2b_client() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=settings.B2B_URL,
            headers={"X-Service-Key": settings.B2B_TO_B2C_KEY}
        )

    @classmethod
    async def get_products(cls, request: Request, sort: str, limit: int, offset: int, q: str = None) -> dict:
        filters = cls.parse_filters(request)
        
        b2b_params = []
        
        if "category_id" in filters:
            b2b_params.append(("category_id", filters["category_id"]))
        if "price_min" in filters:
            b2b_params.append(("min_price", filters["price_min"]))
        if "price_max" in filters:
            b2b_params.append(("max_price", filters["price_max"]))
        if "seller_id" in filters:
            b2b_params.append(("seller_id", filters["seller_id"]))
            
        if "attributes" in filters and isinstance(filters["attributes"], dict):
            for k, v in filters["attributes"].items():
                if isinstance(v, list):
                    for item in v:
                        b2b_params.append((f"filters[{k}]", item))
                else:
                    b2b_params.append((f"filters[{k}]", v))
                    
        if q:
            b2b_params.append(("search", q))
            
        b2b_params.append(("sort", sort))
        b2b_params.append(("limit", str(limit)))
        b2b_params.append(("offset", str(offset)))

        async with await cls.get_b2b_client() as client:
            try:
                resp = await client.get("/api/v1/public/products", params=b2b_params)
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as e:
                raise HTTPException(status_code=e.response.status_code, detail=e.response.json())
            except httpx.RequestError:
                raise HTTPException(status_code=502, detail={"code": "BAD_GATEWAY", "message": "B2B service is unavailable"})

    @classmethod
    async def get_facets(cls, request: Request) -> dict:
        filters = cls.parse_filters(request)
        category_id = request.query_params.get("category_id")
        if "category_id" in filters and not category_id:
            category_id = filters["category_id"]
            
        b2b_params = []
        if category_id:
            b2b_params.append(("category_id", category_id))
            
        if "attributes" in filters and isinstance(filters["attributes"], dict):
            for k, v in filters["attributes"].items():
                if isinstance(v, list):
                    for item in v:
                        b2b_params.append((f"filters[{k}]", item))
                else:
                    b2b_params.append((f"filters[{k}]", v))
        
        # Also support direct filter[brand]=Apple style which B2C canon says: ?category_id={id}&filters[brand]=Apple
        for key, value in request.query_params.multi_items():
            if key.startswith("filters["):
                b2b_params.append((key, value))
                
        async with await cls.get_b2b_client() as client:
            try:
                resp = await client.get("/api/v1/public/facets", params=b2b_params)
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as e:
                raise HTTPException(status_code=e.response.status_code, detail=e.response.json())
            except httpx.RequestError:
                raise HTTPException(status_code=502, detail={"code": "BAD_GATEWAY", "message": "B2B service is unavailable"})

    @classmethod
    async def get_category_filters(cls, category_id: str) -> dict:
        async with await cls.get_b2b_client() as client:
            try:
                resp = await client.get(f"/api/v1/public/categories/{category_id}/filters")
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as e:
                raise HTTPException(status_code=e.response.status_code, detail=e.response.json())
            except httpx.RequestError:
                raise HTTPException(status_code=502, detail={"code": "BAD_GATEWAY", "message": "B2B service is unavailable"})

    @classmethod
    def _map_product_to_b2c(cls, b2b_data: dict) -> dict:
        skus = b2b_data.get("skus", [])
        available_skus = [s for s in skus if s.get("active_quantity", 0) > 0]
        
        has_stock = len(available_skus) > 0
        
        if available_skus:
            cheapest_sku = min(available_skus, key=lambda s: s.get("price", 0) - s.get("discount", 0))
        else:
            cheapest_sku = min(skus, key=lambda s: s.get("price", 0) - s.get("discount", 0)) if skus else None
            
        if cheapest_sku:
            min_price = cheapest_sku.get("price", 0) - cheapest_sku.get("discount", 0)
            old_price = cheapest_sku.get("price", 0) if cheapest_sku.get("discount", 0) > 0 else None
        else:
            min_price = 0
            old_price = None

        mapped_skus = []
        for sku in skus:
            sku_price = sku.get("price", 0)
            sku_discount = sku.get("discount", 0)
            mapped_sku = {
                "id": sku.get("id"),
                "name": sku.get("name"),
                "sku_code": sku.get("article") or "",
                "price": sku_price - sku_discount,
                "old_price": sku_price if sku_discount > 0 else None,
                "available_quantity": sku.get("active_quantity", 0),
                "attributes": {c["name"]: c["value"] for c in sku.get("characteristics", [])},
                "images": [
                    {
                        "id": img.get("id"),
                        "url": img.get("url"),
                        "ordering": img.get("ordering", 0),
                        "alt": img.get("alt", ""),
                        "is_main": img.get("ordering", 0) == 0
                    } for img in sku.get("images", [])
                ]
            }
            mapped_skus.append(mapped_sku)

        b2b_category = b2b_data.get("category", {})
        category_path_str = b2b_category.get("path", "")
        category_path = category_path_str.split(".") if category_path_str else []
        parent_id = category_path[-2] if len(category_path) > 1 else None

        b2b_seller = b2b_data.get("seller")
        if isinstance(b2b_seller, dict):
            seller_mapped = {
                "id": b2b_seller.get("id", b2b_data.get("seller_id")),
                "display_name": b2b_seller.get("display_name", "Продавец")
            }
        else:
            seller_mapped = {
                "id": b2b_data.get("seller_id"),
                "display_name": "Продавец"
            }

        mapped_product = {
            "id": b2b_data.get("id"),
            "name": b2b_data.get("title"),
            "slug": b2b_data.get("slug"),
            "category": {
                "id": b2b_category.get("id"),
                "name": b2b_category.get("name"),
                "level": b2b_category.get("level", 0),
                "path": category_path,
                "parent_id": parent_id
            },
            "min_price": min_price,
            "old_price": old_price,
            "has_stock": has_stock,
            "rating": None,
            "reviews_count": 0,
            "images": [
                {
                    "id": img.get("id"),
                    "url": img.get("url"),
                    "ordering": img.get("ordering", 0),
                    "alt": img.get("alt", ""),
                    "is_main": img.get("ordering", 0) == 0
                } for img in b2b_data.get("images", [])
            ],
            "seller": seller_mapped,
            "description": b2b_data.get("description"),
            "attributes": {c["name"]: c["value"] for c in b2b_data.get("characteristics", [])},
            "skus": mapped_skus
        }
        return mapped_product

    @classmethod
    async def get_product(cls, product_id: str) -> dict:
        async with await cls.get_b2b_client() as client:
            try:
                resp = await client.get(f"/api/v1/public/products/{product_id}")
                resp.raise_for_status()
                data = resp.json()
                
                return cls._map_product_to_b2c(data)

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Product not found"})
                raise HTTPException(status_code=e.response.status_code, detail=e.response.json())
            except httpx.RequestError:
                raise HTTPException(status_code=502, detail={"code": "BAD_GATEWAY", "message": "B2B service is unavailable"})

    @classmethod
    async def get_similar_products(cls, product_id: str, limit: int) -> list:
        async with await cls.get_b2b_client() as client:
            try:
                resp = await client.get(f"/api/v1/public/products/{product_id}/similar", params={"limit": limit})
                resp.raise_for_status()
                data = resp.json()
                
                return [cls._map_product_to_b2c(p) for p in data]

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Product not found"})
                raise HTTPException(status_code=e.response.status_code, detail=e.response.json())
            except httpx.RequestError:
                raise HTTPException(status_code=502, detail={"code": "BAD_GATEWAY", "message": "B2B service is unavailable"})
