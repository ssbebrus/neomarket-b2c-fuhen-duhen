import httpx
import re
from typing import Optional
from fastapi import Request
from src.config import settings
from src.core.exceptions import (
    OrphanCategoryNode,
    AmbiguousBreadcrumbParams,
    MissingBreadcrumbParams,
    CategoryNotFound,
    ProductNotFound,
    B2BServiceUnavailable,
    B2BServiceError,
)

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
                data = resp.json()
            except httpx.HTTPStatusError as e:
                raise B2BServiceError(status_code=e.response.status_code, detail=e.response.json())
            except httpx.RequestError:
                raise B2BServiceUnavailable()

        mapped_items = []
        for item in data.get("items", []):
            mapped_items.append(cls._map_listing_product_to_b2c(item))

        data["items"] = mapped_items
        return data

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
                raise B2BServiceError(status_code=e.response.status_code, detail=e.response.json())
            except httpx.RequestError:
                raise B2BServiceUnavailable()

    @classmethod
    async def get_category_filters(cls, category_id: str) -> dict:
        async with await cls.get_b2b_client() as client:
            try:
                resp = await client.get(f"/api/v1/public/categories/{category_id}/filters")
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as e:
                raise B2BServiceError(status_code=e.response.status_code, detail=e.response.json())
            except httpx.RequestError:
                raise B2BServiceUnavailable()

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
    def _map_listing_product_to_b2c(cls, item: dict) -> dict:
        import uuid
        pid = item.get("id")
        name = item.get("title") or item.get("name") or ""
        slug = item.get("slug") or cls.slugify(name)
        
        min_price = item.get("min_price", 0)
        has_stock = min_price > 0
        
        cover_url = item.get("cover_image")
        images = []
        if cover_url:
            images = [
                {
                    "id": str(uuid.uuid4()),
                    "url": cover_url,
                    "ordering": 0,
                    "alt": "",
                    "is_main": True
                }
            ]
            
        mapped = {
            "id": pid,
            "name": name,
            "slug": slug,
            "category": None,
            "min_price": min_price,
            "old_price": None,
            "has_stock": has_stock,
            "rating": None,
            "reviews_count": 0,
            "images": images,
            "seller": None
        }
        
        return mapped

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
                    raise ProductNotFound()
                raise B2BServiceError(status_code=e.response.status_code, detail=e.response.json())
            except httpx.RequestError:
                raise B2BServiceUnavailable()

    @classmethod
    async def get_similar_products(cls, product_id: str, limit: int) -> list:
        async with await cls.get_b2b_client() as client:
            try:
                resp = await client.get(f"/api/v1/public/products/{product_id}/similar", params={"limit": limit})
                resp.raise_for_status()
                similar_data = resp.json()
                if not similar_data:
                    return []
                
                product_ids = [p["id"] for p in similar_data]
                batch_resp = await client.post("/api/v1/public/products/batch", json={"product_ids": product_ids})
                batch_resp.raise_for_status()
                full_products = batch_resp.json()
                
                full_products_by_id = {p["id"]: p for p in full_products}
                ordered_full_products = []
                for p_short in similar_data:
                    p_id = p_short["id"]
                    if p_id in full_products_by_id:
                        ordered_full_products.append(full_products_by_id[p_id])
                
                return [cls._map_product_to_b2c(p) for p in ordered_full_products]

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    raise ProductNotFound()
                raise B2BServiceError(status_code=e.response.status_code, detail=e.response.json())
            except httpx.RequestError:
                raise B2BServiceUnavailable()

    @staticmethod
    def slugify(text: str) -> str:
        # Russian transliteration dictionary
        translit_dict = {
            'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'yo',
            'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
            'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
            'ф': 'f', 'х': 'kh', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'shch',
            'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya'
        }
        text = text.lower().strip()
        result = []
        for char in text:
            if char in translit_dict:
                result.append(translit_dict[char])
            elif char.isalnum():
                result.append(char)
            elif char in (' ', '-', '_'):
                result.append('-')
            else:
                result.append('-')
        slug = ''.join(result)
        slug = re.sub(r'-+', '-', slug)
        return slug.strip('-')

    @staticmethod
    def check_orphan_node(category_id: str, all_categories_by_id: dict) -> None:
        """
        Check if category_id or any of its ancestors in its path is missing from all_categories_by_id.
        """
        cat = all_categories_by_id.get(category_id)
        if not cat:
            return
        path_str = cat.get("path", "")
        if not path_str:
            return
        path_parts = path_str.split(".")
        for part in path_parts:
            if part not in all_categories_by_id:
                raise OrphanCategoryNode()

    @classmethod
    async def get_categories(cls) -> list:
        async with await cls.get_b2b_client() as client:
            try:
                resp = await client.get("/api/v1/categories")
                resp.raise_for_status()
                b2b_cats = resp.json()
            except httpx.HTTPStatusError as e:
                raise B2BServiceError(status_code=e.response.status_code, detail=e.response.json())
            except httpx.RequestError:
                raise B2BServiceUnavailable()

        mapped = []
        for cat in b2b_cats:
            path_str = cat.get("path", "")
            path_parts = path_str.split(".") if path_str else []
            parent_id = path_parts[-2] if len(path_parts) > 1 else None
            mapped.append({
                "id": cat.get("id"),
                "name": cat.get("name"),
                "level": cat.get("level", 0),
                "path": path_parts,
                "parent_id": parent_id
            })
        return mapped

    @classmethod
    async def get_categories_tree(cls) -> list:
        async with await cls.get_b2b_client() as client:
            try:
                resp = await client.get("/api/v1/categories")
                resp.raise_for_status()
                b2b_cats = resp.json()
            except httpx.HTTPStatusError as e:
                raise B2BServiceError(status_code=e.response.status_code, detail=e.response.json())
            except httpx.RequestError:
                raise B2BServiceUnavailable()

        # Build ID lookup and verify orphan nodes
        all_cats_by_id = {c["id"]: c for c in b2b_cats}
        for cat_id, cat in all_cats_by_id.items():
            path_str = cat.get("path", "")
            if path_str:
                path_parts = path_str.split(".")
                for part in path_parts:
                    if part not in all_cats_by_id:
                        raise OrphanCategoryNode()

        # Build nodes
        nodes = {}
        for cat in b2b_cats:
            path_str = cat.get("path", "")
            path_parts = path_str.split(".") if path_str else []
            parent_id = path_parts[-2] if len(path_parts) > 1 else None
            nodes[cat["id"]] = {
                "id": cat["id"],
                "name": cat["name"],
                "level": cat["level"],
                "path": path_parts,
                "parent_id": parent_id,
                "children": []
            }

        roots = []
        for cat_id, node in nodes.items():
            parent_id = node["parent_id"]
            if parent_id and parent_id in nodes:
                nodes[parent_id]["children"].append(node)
            else:
                roots.append(node)
        return roots

    @classmethod
    async def get_category_detail(cls, category_id: str, include_product_count: bool = False) -> dict:
        async with await cls.get_b2b_client() as client:
            try:
                resp = await client.get("/api/v1/categories")
                resp.raise_for_status()
                b2b_cats = resp.json()
            except httpx.HTTPStatusError as e:
                raise B2BServiceError(status_code=e.response.status_code, detail=e.response.json())
            except httpx.RequestError:
                raise B2BServiceUnavailable()

        all_cats_by_id = {c["id"]: c for c in b2b_cats}
        if category_id not in all_cats_by_id:
            raise CategoryNotFound()

        # Orphan check
        cls.check_orphan_node(category_id, all_cats_by_id)

        cat = all_cats_by_id[category_id]
        path_str = cat.get("path", "")
        path_parts = path_str.split(".") if path_str else []
        parent_id = path_parts[-2] if len(path_parts) > 1 else None

        parent_node = None
        if parent_id and parent_id in all_cats_by_id:
            p_cat = all_cats_by_id[parent_id]
            parent_node = {
                "id": p_cat["id"],
                "name": p_cat["name"],
                "slug": cls.slugify(p_cat["name"])
            }

        slug = cls.slugify(cat["name"])

        # Fetch product count
        product_count = 0
        if include_product_count:
            async with await cls.get_b2b_client() as client:
                try:
                    p_resp = await client.get("/api/v1/public/products", params={"category_id": category_id, "limit": 1})
                    p_resp.raise_for_status()
                    product_count = p_resp.json().get("total_count", 0)
                except Exception:
                    product_count = 0

        created_at = cat.get("created_at") or "2024-01-15T10:30:00Z"
        updated_at = cat.get("updated_at") or "2024-03-01T14:20:00Z"

        return {
            "id": cat["id"],
            "name": cat["name"],
            "slug": slug,
            "description": f"Описание категории {cat['name']}",
            "parent": parent_node,
            "product_count": product_count,
            "seo": {
                "title": f"Купить {cat['name'].lower()} в интернет-магазине | NeoMarket",
                "description": f"{cat['name']} по выгодным ценам. Бесплатная доставка.",
                "keywords": [cat['name'].lower(), f"купить {cat['name'].lower()}"]
            },
            "meta_tags": {
                "og_title": f"{cat['name']} | NeoMarket",
                "og_description": f"Купить {cat['name'].lower()} в интернет-магазине."
            },
            "image_url": f"https://cdn.neomarket.ru/categories/{slug}.jpg",
            "is_active": cat.get("is_active", True),
            "created_at": created_at,
            "updated_at": updated_at
        }

    @classmethod
    async def get_breadcrumbs(cls, category_id: Optional[str] = None, product_id: Optional[str] = None) -> dict:
        if category_id is not None and product_id is not None:
            raise AmbiguousBreadcrumbParams()
        if category_id is None and product_id is None:
            raise MissingBreadcrumbParams()

        resolved_category_id = category_id
        resolved_via = "category_id"

        if product_id is not None:
            resolved_via = "product_id"
            async with await cls.get_b2b_client() as client:
                try:
                    p_resp = await client.get(f"/api/v1/public/products/{product_id}")
                    if p_resp.status_code == 404:
                        raise ProductNotFound()
                    p_resp.raise_for_status()
                    product_data = p_resp.json()
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 404:
                        raise ProductNotFound()
                    raise B2BServiceError(status_code=e.response.status_code, detail=e.response.json())
                except httpx.RequestError:
                    raise B2BServiceUnavailable()

            category_obj = product_data.get("category")
            if not category_obj or "id" not in category_obj:
                raise CategoryNotFound()
            resolved_category_id = category_obj["id"]

        # Fetch all categories
        async with await cls.get_b2b_client() as client:
            try:
                resp = await client.get("/api/v1/categories")
                resp.raise_for_status()
                b2b_cats = resp.json()
            except httpx.HTTPStatusError as e:
                raise B2BServiceError(status_code=e.response.status_code, detail=e.response.json())
            except httpx.RequestError:
                raise B2BServiceUnavailable()

        all_cats_by_id = {c["id"]: c for c in b2b_cats}
        if resolved_category_id not in all_cats_by_id:
            raise CategoryNotFound()

        # Check orphan node
        cls.check_orphan_node(resolved_category_id, all_cats_by_id)

        target_cat = all_cats_by_id[resolved_category_id]
        path_str = target_cat.get("path", "")
        path_parts = path_str.split(".") if path_str else []

        breadcrumbs_data = []
        cumulative_url = "/catalog"
        for i, path_part_id in enumerate(path_parts):
            if path_part_id not in all_cats_by_id:
                raise OrphanCategoryNode()
            cat_item = all_cats_by_id[path_part_id]
            slug = cls.slugify(cat_item["name"])
            cumulative_url = f"{cumulative_url}/{slug}"
            breadcrumbs_data.append({
                "id": cat_item["id"],
                "slug": slug,
                "name": cat_item["name"],
                "url": cumulative_url,
                "level": i,
                "is_current": (path_part_id == resolved_category_id)
            })

        return {
            "data": breadcrumbs_data,
            "meta": {
                "resolved_via": resolved_via,
                "category_id": resolved_category_id
            }
        }

