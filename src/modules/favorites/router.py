import jwt
import uuid
from fastapi import APIRouter, Depends, HTTPException, Security, Response
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import UUID4
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db.database import get_db
from src.modules.favorites.service import FavoritesService

router = APIRouter()
security = HTTPBearer()

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
    user_id: str = None,  # Ignored (IDOR protection validation test)
    current_user_id: uuid.UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """
    GET /api/v1/favorites
    Retrieves the paginated list of catalog product cards in user's favorites.
    """
    return await FavoritesService.get_favorites(db, current_user_id, limit, offset)

@router.post("/favorites/{product_id}")
async def add_to_favorites_post(
    product_id: UUID4,
    user_id: str = None,  # Ignored (IDOR protection validation test)
    current_user_id: uuid.UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """
    POST /api/v1/favorites/{product_id}
    Adds a product to favorites. Returns 201 on first addition, 200 on repeat.
    """
    favorite, created = await FavoritesService.add_to_favorites(db, current_user_id, product_id)
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
    user_id: str = None,  # Ignored (IDOR protection validation test)
    current_user_id: uuid.UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """
    PUT /api/v1/favorites/{product_id}
    Adds a product to favorites (idempotently). Returns 204.
    """
    await FavoritesService.add_to_favorites(db, current_user_id, product_id)
    return Response(status_code=204)

@router.delete("/favorites/{product_id}")
async def remove_from_favorites(
    product_id: UUID4,
    user_id: str = None,  # Ignored (IDOR protection validation test)
    current_user_id: uuid.UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """
    DELETE /api/v1/favorites/{product_id}
    Removes a product from favorites. Returns 204.
    """
    await FavoritesService.remove_from_favorites(db, current_user_id, product_id)
    return Response(status_code=204)
