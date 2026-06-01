from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import UUID4

from .schemas import CatalogProductDetail, CatalogProductCard, CategoryRef, CategoryTreeNode, CategoryDetail, BreadcrumbsResponse
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
    if q is not None:
        q_stripped = q.strip()
        if len(q_stripped) < 3:
            return JSONResponse(
                status_code=400,
                content={
                    "code": "INVALID_REQUEST", 
                    "message": "Search query must be at least 3 characters"
                }
            )
        if len(q_stripped) > 255:
            return JSONResponse(
                status_code=400,
                content={
                    "code": "INVALID_REQUEST", 
                    "message": "Search query must be at most 255 characters"
                }
            )

    if sort not in ALLOWED_SORTS:
        return JSONResponse(
            status_code=400,
            content={
                "code": "INVALID_REQUEST", 
                "message": "Invalid sort parameter. Allowed: price_asc, price_desc, popularity, new"
            }
        )
    
    return await CatalogService.get_products(
        request=request,
        sort=sort,
        limit=limit,
        offset=offset,
        q=q
    )

@router.get("/catalog/products/{product_id}", response_model=CatalogProductDetail)
async def get_catalog_product(product_id: UUID4):
    return await CatalogService.get_product(str(product_id))

@router.get("/catalog/products/{product_id}/similar", response_model=list[CatalogProductCard])
async def get_catalog_product_similar(product_id: UUID4, limit: int = 10):
    return await CatalogService.get_similar_products(str(product_id), limit)

@router.get("/catalog/facets")
async def get_catalog_facets(category_id: UUID4, request: Request):
    return await CatalogService.get_facets(request)

@router.get("/catalog/categories/{category_id}/filters")
async def get_catalog_category_filters(category_id: UUID4):
    return await CatalogService.get_category_filters(str(category_id))

@router.get("/catalog/categories", response_model=list[CategoryRef])
async def get_catalog_categories():
    return await CatalogService.get_categories()

@router.get("/catalog/categories/tree", response_model=list[CategoryTreeNode])
async def get_catalog_categories_tree():
    return await CatalogService.get_categories_tree()

@router.get("/catalog/categories/{category_id}", response_model=CategoryDetail)
async def get_catalog_category_detail(category_id: UUID4, include_product_count: bool = False):
    return await CatalogService.get_category_detail(str(category_id), include_product_count)

@router.get("/catalog/breadcrumbs", response_model=BreadcrumbsResponse)
async def get_catalog_breadcrumbs(
    category_id: UUID4 = None,
    product_id: UUID4 = None
):
    return await CatalogService.get_breadcrumbs(
        category_id=str(category_id) if category_id else None,
        product_id=str(product_id) if product_id else None
    )

