import jwt
import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Security, Response
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import UUID4, BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.core.exceptions import ProductNotFound, B2BServiceUnavailable, SubscriptionAlreadyExists
from src.db.database import get_db
from src.modules.favorites.service import FavoritesService

router = APIRouter()
security = HTTPBearer()

class SubscribeRequest(BaseModel):
    events: List[str] = Field(default_factory=lambda: ["BACK_IN_STOCK", "PRICE_DROP"])

async def get_current_user_id(credentials: HTTPAuthorizationCredentials = Security(security)) -> uuid.UUID:
    """
    Dependency to validate JWT access token and extract the user's sub UUID claim.
    Returns 401 on failure.
    """
    token = credentials.credentials
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(
                status_code=401,
                detail={"code": "UNAUTHORIZED", "message": "Token is missing sub claim"}
            )
        return uuid.UUID(user_id)
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

@router.get("/favorites")
async def get_favorites(
    limit: int = 20,
    offset: int = 0,
    current_user_id: uuid.UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """
    GET /api/v1/favorites
    Retrieves the paginated list of catalog product cards in user's favorites.
    """
    try:
        return await FavoritesService.get_favorites(db, current_user_id, limit, offset)
    except B2BServiceUnavailable:
        raise HTTPException(
            status_code=503,
            detail={"code": "B2B_UNAVAILABLE", "message": "B2B service is unavailable"}
        )

@router.post("/favorites/{product_id}")
async def add_to_favorites_post(
    product_id: UUID4,
    current_user_id: uuid.UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """
    POST /api/v1/favorites/{product_id}
    Adds a product to favorites. Returns 201 on first addition, 200 on repeat.
    """
    try:
        favorite, created = await FavoritesService.add_to_favorites(db, current_user_id, product_id)
    except ProductNotFound:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "Product not found"}
        )
    except B2BServiceUnavailable:
        raise HTTPException(
            status_code=503,
            detail={"code": "B2B_UNAVAILABLE", "message": "B2B service is unavailable"}
        )
    content = {
        "id": str(favorite.id),
        "user_id": str(favorite.user_id),
        "product_id": str(favorite.product_id),
        "added_at": favorite.added_at.isoformat()
    }
    status_code = 201 if created else 200
    return JSONResponse(status_code=status_code, content=content)

@router.put("/favorites/{product_id}")
async def add_to_favorites_put(
    product_id: UUID4,
    current_user_id: uuid.UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """
    PUT /api/v1/favorites/{product_id}
    Adds a product to favorites (idempotently). Returns 204.
    """
    try:
        await FavoritesService.add_to_favorites(db, current_user_id, product_id)
    except ProductNotFound:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "Product not found"}
        )
    except B2BServiceUnavailable:
        raise HTTPException(
            status_code=503,
            detail={"code": "B2B_UNAVAILABLE", "message": "B2B service is unavailable"}
        )
    return Response(status_code=204)

@router.delete("/favorites/{product_id}")
async def remove_from_favorites(
    product_id: UUID4,
    current_user_id: uuid.UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """
    DELETE /api/v1/favorites/{product_id}
    Removes a product from favorites. Returns 204.
    """
    await FavoritesService.remove_from_favorites(db, current_user_id, product_id)
    return Response(status_code=204)

@router.post("/favorites/{product_id}/subscribe")
async def subscribe_to_product(
    product_id: UUID4,
    body: Optional[SubscribeRequest] = None,
    current_user_id: uuid.UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """
    POST /api/v1/favorites/{product_id}/subscribe
    Subscribes to notifications for a product.
    """
    # If body is not provided, use default events
    if body is None:
        body = SubscribeRequest()

    if not body.events:
        raise HTTPException(
            status_code=400,
            detail={"code": "VALIDATION_ERROR", "message": "events list cannot be empty"}
        )

    valid_events = {"BACK_IN_STOCK", "PRICE_DROP"}
    for event in body.events:
        if event not in valid_events:
            raise HTTPException(
                status_code=400,
                detail={"code": "VALIDATION_ERROR", "message": f"Invalid event value: {event}"}
            )

    try:
        subscription = await FavoritesService.subscribe_to_product(
            db, current_user_id, product_id, body.events
        )
    except ProductNotFound:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "Product not found"}
        )
    except SubscriptionAlreadyExists:
        raise HTTPException(
            status_code=409,
            detail={"code": "SUBSCRIPTION_ALREADY_EXISTS", "message": "Subscription already exists"}
        )
    except B2BServiceUnavailable:
        raise HTTPException(
            status_code=503,
            detail={"code": "B2B_UNAVAILABLE", "message": "B2B service is unavailable"}
        )

    content = {
        "id": str(subscription.id),
        "user_id": str(subscription.user_id),
        "product_id": str(subscription.product_id),
        "events": subscription.notify_on,
        "created_at": subscription.created_at.isoformat()
    }
    return JSONResponse(status_code=201, content=content)

@router.delete("/favorites/{product_id}/subscribe")
async def unsubscribe_from_product(
    product_id: UUID4,
    current_user_id: uuid.UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """
    DELETE /api/v1/favorites/{product_id}/subscribe
    Unsubscribes from notifications for a product (idempotent).
    """
    await FavoritesService.unsubscribe_from_product(db, current_user_id, product_id)
    return Response(status_code=204)

