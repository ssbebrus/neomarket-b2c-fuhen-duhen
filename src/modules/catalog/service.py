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
