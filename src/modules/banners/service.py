import uuid
from datetime import datetime, timezone
from typing import List, Optional
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_

from src.modules.banners.models import Banner, BannerEvent
from src.modules.banners.schemas import BannerResponse, BannerEventCreate, BannerCreateRequest

class BannersService:
    @staticmethod
    async def create_banner(db: AsyncSession, schema: BannerCreateRequest) -> BannerResponse:
        banner = Banner(
            title=schema.title,
            image_url=schema.image_url,
            link=schema.link,
            priority=schema.priority,
            is_active=schema.is_active,
            start_at=schema.start_at,
            end_at=schema.end_at
        )
        db.add(banner)
        await db.commit()
        await db.refresh(banner)
        return BannerResponse(
            id=banner.id,
            title=banner.title,
            image_url=banner.image_url,
            link=banner.link,
            ordering=banner.priority,
            active_from=banner.start_at,
            active_to=banner.end_at
        )

    @staticmethod
    async def get_active_banners(db: AsyncSession) -> List[BannerResponse]:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        stmt = select(Banner).where(
            Banner.is_active == True,
            or_(Banner.start_at == None, Banner.start_at <= now),
            or_(Banner.end_at == None, Banner.end_at >= now)
        ).order_by(Banner.priority.asc())
        
        result = await db.execute(stmt)
        banners = result.scalars().all()
        
        return [
            BannerResponse(
                id=b.id,
                title=b.title,
                image_url=b.image_url,
                link=b.link,
                ordering=b.priority,
                active_from=b.start_at,
                active_to=b.end_at
            )
            for b in banners
        ]

    @staticmethod
    async def create_banner_events(
        db: AsyncSession,
        events: List[BannerEventCreate],
        user_id: Optional[uuid.UUID]
    ) -> None:
        if not events:
            raise HTTPException(
                status_code=400,
                detail={"code": "EMPTY_EVENTS", "message": "Events list cannot be empty"}
            )
            
        banner_ids = list({e.banner_id for e in events})
        
        stmt = select(Banner.id).where(Banner.id.in_(banner_ids))
        result = await db.execute(stmt)
        existing_ids = set(result.scalars().all())
        
        for e in events:
            if e.banner_id not in existing_ids:
                raise HTTPException(
                    status_code=400,
                    detail={"code": "BANNER_NOT_FOUND", "message": f"Banner {e.banner_id} not found"}
                )
                
        new_events = [
            BannerEvent(
                banner_id=e.banner_id,
                user_id=user_id,
                event=e.event,
                timestamp=e.timestamp
            )
            for e in events
        ]
        
        db.add_all(new_events)
        await db.commit()

