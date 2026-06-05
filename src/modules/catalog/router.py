from contextlib import contextmanager
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from pydantic import UUID4

from src.core.exceptions import (
    ProductNotFound,
    CategoryNotFound,
    OrphanCategoryNode,
    AmbiguousBreadcrumbParams,
    MissingBreadcrumbParams,
    B2BServiceUnavailable,
    B2BServiceError,
)
from .schemas import CatalogProductDetail, CatalogProductCard, CategoryRef, CategoryTreeNode, CategoryDetail, BreadcrumbsResponse, PaginatedCatalogProducts
from .service import CatalogService, ALLOWED_SORTS

router = APIRouter()

@contextmanager
def handle_catalog_exceptions():
    try:
        yield
    except ProductNotFound:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "Product not found"}
        )
    except CategoryNotFound:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "Category not found"}
        )
    except OrphanCategoryNode:
        raise HTTPException(
            status_code=422,
            detail={"code": "ORPHAN_NODE", "message": "category hierarchy is broken"}
        )
    except AmbiguousBreadcrumbParams:
        raise HTTPException(
            status_code=400,
            detail={"code": "AMBIGUOUS_PARAM", "message": "only one of category_id or product_id must be provided"}
        )
    except MissingBreadcrumbParams:
        raise HTTPException(
            status_code=400,
            detail={"code": "MISSING_PARAM", "message": "category_id or product_id must be provided"}
        )
    except B2BServiceUnavailable:
        raise HTTPException(
            status_code=502,
            detail={"code": "BAD_GATEWAY", "message": "B2B service is unavailable"}
        )
    except B2BServiceError as e:
        raise HTTPException(
            status_code=e.status_code,
            detail=e.detail
        )


@router.get("/catalog/products", response_model=PaginatedCatalogProducts)
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
    
    with handle_catalog_exceptions():
        return await CatalogService.get_products(
            request=request,
            sort=sort,
            limit=limit,
            offset=offset,
            q=q
        )


@router.get("/catalog/products/{product_id}", response_model=CatalogProductDetail)
async def get_catalog_product(product_id: UUID4):
    with handle_catalog_exceptions():
        return await CatalogService.get_product(str(product_id))


@router.get("/catalog/products/{product_id}/similar", response_model=list[CatalogProductCard])
async def get_catalog_product_similar(product_id: UUID4, limit: int = 10):
    with handle_catalog_exceptions():
        return await CatalogService.get_similar_products(str(product_id), limit)


@router.get("/catalog/facets")
async def get_catalog_facets(category_id: UUID4, request: Request):
    with handle_catalog_exceptions():
        return await CatalogService.get_facets(request)


@router.get("/catalog/categories/{category_id}/filters")
async def get_catalog_category_filters(category_id: UUID4):
    with handle_catalog_exceptions():
        return await CatalogService.get_category_filters(str(category_id))


@router.get("/catalog/categories", response_model=list[CategoryRef])
async def get_catalog_categories():
    with handle_catalog_exceptions():
        return await CatalogService.get_categories()


@router.get("/catalog/categories/tree", response_model=list[CategoryTreeNode])
async def get_catalog_categories_tree():
    with handle_catalog_exceptions():
        return await CatalogService.get_categories_tree()


@router.get("/catalog/categories/{category_id}", response_model=CategoryDetail)
async def get_catalog_category_detail(category_id: UUID4, include_product_count: bool = False):
    with handle_catalog_exceptions():
        return await CatalogService.get_category_detail(str(category_id), include_product_count)


@router.get("/catalog/breadcrumbs", response_model=BreadcrumbsResponse)
async def get_catalog_breadcrumbs(
    category_id: UUID4 = None,
    product_id: UUID4 = None
):
    with handle_catalog_exceptions():
        return await CatalogService.get_breadcrumbs(
            category_id=str(category_id) if category_id else None,
            product_id=str(product_id) if product_id else None
        )
