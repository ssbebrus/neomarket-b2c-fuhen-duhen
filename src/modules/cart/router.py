import jwt
import uuid
from typing import Optional, Tuple
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from fastapi.security.utils import get_authorization_scheme_param
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.core.exceptions import (
    B2BServiceUnavailable,
    SKUNotFound,
    ProductUnavailable,
    CartItemNotFound,
    InsufficientStock
)
from src.db.database import get_db
from src.modules.cart.service import CartService
from src.modules.cart.schemas import (
    CartItemAddRequest,
    CartItemUpdateRequest,
    CartResponse,
    CartValidationResponse
)

router = APIRouter()

async def get_cart_identity(request: Request) -> Tuple[Optional[uuid.UUID], Optional[str]]:
    """
    Extracts the user identity:
    1. If Authorization Bearer token is provided, decode and extract user ID from sub claim.
    2. Otherwise, check X-Session-Id header.
    If both are missing, raises 400 Validation Error.
    """
    auth_header = request.headers.get("Authorization")
    user_id = None
    if auth_header:
        scheme, token = get_authorization_scheme_param(auth_header)
        if scheme.lower() == "bearer":
            try:
                payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
                sub = payload.get("sub")
                if not sub:
                    raise HTTPException(
                        status_code=401,
                        detail={"code": "UNAUTHORIZED", "message": "Token is missing sub claim"}
                    )
                user_id = uuid.UUID(sub)
            except jwt.ExpiredSignatureError:
                raise HTTPException(
                    status_code=401,
                    detail={"code": "UNAUTHORIZED", "message": "Token has expired"}
                )
            except jwt.PyJWTError as e:
                raise HTTPException(
                    status_code=401,
                    detail={"code": "UNAUTHORIZED", "message": f"Invalid token: {str(e)}"}
                )
            except ValueError:
                raise HTTPException(
                    status_code=401,
                    detail={"code": "UNAUTHORIZED", "message": "Invalid sub claim format"}
                )

    session_id = request.headers.get("X-Session-Id")

    # If neither is present, raise 400
    if not user_id and not session_id:
        raise HTTPException(
            status_code=400,
            detail={"code": "VALIDATION_ERROR", "message": "Authorization or X-Session-Id header is required"}
        )

    # Check for invalid UUID in session_id if present
    if session_id:
        try:
            uuid.UUID(session_id)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={"code": "VALIDATION_ERROR", "message": "Invalid X-Session-Id format"}
            )

    return user_id, session_id


async def get_merge_identity(request: Request) -> Tuple[uuid.UUID, str]:
    """
    Special identity extractor for cart merge which requires both JWT and X-Session-Id.
    """
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        raise HTTPException(
            status_code=401,
            detail={"code": "UNAUTHORIZED", "message": "Authorization header is required for cart merge"}
        )

    scheme, token = get_authorization_scheme_param(auth_header)
    if scheme.lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail={"code": "UNAUTHORIZED", "message": "Bearer authentication required"}
        )

    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        sub = payload.get("sub")
        if not sub:
            raise HTTPException(
                status_code=401,
                detail={"code": "UNAUTHORIZED", "message": "Token is missing sub claim"}
            )
        user_id = uuid.UUID(sub)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=401,
            detail={"code": "UNAUTHORIZED", "message": "Token has expired"}
        )
    except jwt.PyJWTError as e:
        raise HTTPException(
            status_code=401,
            detail={"code": "UNAUTHORIZED", "message": f"Invalid token: {str(e)}"}
        )
    except ValueError:
        raise HTTPException(
            status_code=401,
            detail={"code": "UNAUTHORIZED", "message": "Invalid sub claim format"}
        )

    session_id = request.headers.get("X-Session-Id")
    if not session_id:
        raise HTTPException(
            status_code=400,
            detail={"code": "VALIDATION_ERROR", "message": "X-Session-Id header is required"}
        )

    try:
        uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"code": "VALIDATION_ERROR", "message": "Invalid X-Session-Id format"}
        )

    return user_id, session_id


@router.get("/cart", response_model=CartResponse)
async def get_cart(
    identity: Tuple[Optional[uuid.UUID], Optional[str]] = Depends(get_cart_identity),
    db: AsyncSession = Depends(get_db)
):
    """
    GET /api/v1/cart
    Retrieves the enriched shopping cart.
    """
    user_id, session_id = identity
    try:
        return await CartService.get_cart(db, user_id, session_id)
    except SKUNotFound:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "SKU not found"}
        )
    except B2BServiceUnavailable:
        raise HTTPException(
            status_code=503,
            detail={"code": "B2B_UNAVAILABLE", "message": "B2B service is unavailable"}
        )


@router.delete("/cart")
async def clear_cart(
    identity: Tuple[Optional[uuid.UUID], Optional[str]] = Depends(get_cart_identity),
    db: AsyncSession = Depends(get_db)
):
    """
    DELETE /api/v1/cart
    Clears the shopping cart.
    """
    user_id, session_id = identity
    await CartService.clear_cart(db, user_id, session_id)
    return Response(status_code=204)


@router.post("/cart/items", response_model=CartResponse)
async def add_item_to_cart(
    body: CartItemAddRequest,
    identity: Tuple[Optional[uuid.UUID], Optional[str]] = Depends(get_cart_identity),
    db: AsyncSession = Depends(get_db)
):
    """
    POST /api/v1/cart/items
    Adds a SKU to the shopping cart.
    """
    user_id, session_id = identity
    try:
        return await CartService.add_item(db, user_id, session_id, body.sku_id, body.quantity)
    except SKUNotFound:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "SKU not found"}
        )
    except ProductUnavailable:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "Product is unavailable"}
        )
    except InsufficientStock:
        raise HTTPException(
            status_code=409,
            detail={"code": "INSUFFICIENT_STOCK", "message": "Insufficient stock"}
        )
    except B2BServiceUnavailable:
        raise HTTPException(
            status_code=503,
            detail={"code": "B2B_UNAVAILABLE", "message": "B2B service is unavailable"}
        )


@router.patch("/cart/items/{sku_id}", response_model=CartResponse)
async def update_item_quantity(
    sku_id: uuid.UUID,
    body: CartItemUpdateRequest,
    identity: Tuple[Optional[uuid.UUID], Optional[str]] = Depends(get_cart_identity),
    db: AsyncSession = Depends(get_db)
):
    """
    PATCH /api/v1/cart/items/{sku_id}
    Changes the quantity of a SKU in the cart.
    """
    user_id, session_id = identity
    try:
        return await CartService.update_item_quantity(db, user_id, session_id, sku_id, body.quantity)
    except CartItemNotFound:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "Cart item not found"}
        )
    except SKUNotFound:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "SKU not found"}
        )
    except InsufficientStock:
        raise HTTPException(
            status_code=409,
            detail={"code": "INSUFFICIENT_STOCK", "message": "Insufficient stock"}
        )
    except B2BServiceUnavailable:
        raise HTTPException(
            status_code=503,
            detail={"code": "B2B_UNAVAILABLE", "message": "B2B service is unavailable"}
        )


@router.delete("/cart/items/{sku_id}", response_model=CartResponse)
async def delete_item_from_cart(
    sku_id: uuid.UUID,
    identity: Tuple[Optional[uuid.UUID], Optional[str]] = Depends(get_cart_identity),
    db: AsyncSession = Depends(get_db)
):
    """
    DELETE /api/v1/cart/items/{sku_id}
    Deletes a SKU from the cart.
    """
    user_id, session_id = identity
    try:
        return await CartService.delete_item(db, user_id, session_id, sku_id)
    except SKUNotFound:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "SKU not found"}
        )
    except B2BServiceUnavailable:
        raise HTTPException(
            status_code=503,
            detail={"code": "B2B_UNAVAILABLE", "message": "B2B service is unavailable"}
        )


@router.post("/cart/validate", response_model=CartValidationResponse)
async def validate_cart(
    identity: Tuple[Optional[uuid.UUID], Optional[str]] = Depends(get_cart_identity),
    db: AsyncSession = Depends(get_db)
):
    """
    POST /api/v1/cart/validate
    Validates cart details.
    """
    user_id, session_id = identity
    try:
        return await CartService.validate_cart(db, user_id, session_id)
    except SKUNotFound:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "SKU not found"}
        )
    except B2BServiceUnavailable:
        raise HTTPException(
            status_code=503,
            detail={"code": "B2B_UNAVAILABLE", "message": "B2B service is unavailable"}
        )


@router.post("/cart/merge", response_model=CartResponse)
async def merge_carts(
    identity: Tuple[uuid.UUID, str] = Depends(get_merge_identity),
    db: AsyncSession = Depends(get_db)
):
    """
    POST /api/v1/cart/merge
    Merges guest cart items into authenticated user's cart.
    """
    user_id, session_id = identity
    try:
        return await CartService.merge_carts(db, user_id, session_id)
    except SKUNotFound:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "SKU not found"}
        )
    except B2BServiceUnavailable:
        raise HTTPException(
            status_code=503,
            detail={"code": "B2B_UNAVAILABLE", "message": "B2B service is unavailable"}
        )
