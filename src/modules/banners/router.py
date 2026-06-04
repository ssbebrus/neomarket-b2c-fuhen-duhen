import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.database import get_db
from src.modules.banners.schemas import BannerResponse, BannerEventsRequest, BannerCreateRequest
from src.modules.banners.service import BannersService

router = APIRouter()

async def get_optional_user_id(request: Request) -> Optional[uuid.UUID]:
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        return None
        
    from fastapi.security.utils import get_authorization_scheme_param
    import jwt
    from src.config import settings
    
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
        return uuid.UUID(sub)
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

@router.get("/catalog/banners", response_model=List[BannerResponse])
async def get_banners(db: AsyncSession = Depends(get_db)):
    return await BannersService.get_active_banners(db)

@router.post("/catalog/banners", response_model=BannerResponse, status_code=201)
async def post_create_banner(
    body: BannerCreateRequest,
    db: AsyncSession = Depends(get_db)
):
    return await BannersService.create_banner(db, body)

@router.post("/catalog/banners/events", status_code=201)
async def post_banner_events(
    body: BannerEventsRequest,
    user_id: Optional[uuid.UUID] = Depends(get_optional_user_id),
    db: AsyncSession = Depends(get_db)
):
    await BannersService.create_banner_events(db, body.events, user_id)
    return {"status": "ok"}

