from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import UUID4

from .service import CatalogService, ALLOWED_SORTS

router = APIRouter()

@router.get("/catalog/products")
async def get_catalog_products(
    request: Request,
    sort: str = "popularity",
    limit: int = 20,
    offset: int = 0,
    q: str = None
):
    if sort not in ALLOWED_SORTS:
        return JSONResponse(
            status_code=400,
            content={
                "code": "INVALID_REQUEST", 
                "message": "Invalid sort parameter. Allowed: rating, popularity, price_asc, price_desc, date_desc, discount_desc"
            }
        )
    
    return await CatalogService.get_products(
        request=request,
        sort=sort,
        limit=limit,
        offset=offset,
        q=q
    )

@router.get("/catalog/facets")
async def get_catalog_facets(request: Request):
    return await CatalogService.get_facets(request)

@router.get("/catalog/categories/{category_id}/filters")
async def get_catalog_category_filters(category_id: UUID4):
    return await CatalogService.get_category_filters(str(category_id))
