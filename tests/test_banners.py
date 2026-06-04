import pytest
import uuid
import jwt
from datetime import datetime, timezone, timedelta
from httpx import AsyncClient
from sqlalchemy import select

from src.config import settings
from src.modules.banners.models import Banner, BannerEvent

USER_ID = uuid.uuid4()

def generate_token(user_id: uuid.UUID) -> str:
    payload = {"sub": str(user_id)}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)

@pytest.mark.asyncio
async def test_active_banners_returned_sorted_by_priority(client: AsyncClient, test_db):
    """
    happy: active_banners_returned_sorted_by_priority
    Only active banners in schedule are returned, sorted by priority.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    
    # 1. Active & currently scheduled (priority 10)
    banner1 = Banner(
        title="Banner 10",
        image_url="/cdn/b10.jpg",
        link="/link10",
        priority=10,
        is_active=True,
        start_at=now - timedelta(days=1),
        end_at=now + timedelta(days=1)
    )
    # 2. Active & currently scheduled (priority 5) - should come first
    banner2 = Banner(
        title="Banner 5",
        image_url="/cdn/b5.jpg",
        link="/link5",
        priority=5,
        is_active=True,
        start_at=None,
        end_at=None
    )
    # 3. Inactive banner
    banner3 = Banner(
        title="Inactive Banner",
        image_url="/cdn/binactive.jpg",
        link="/linkinactive",
        priority=1,
        is_active=False,
        start_at=None,
        end_at=None
    )
    # 4. Expired banner
    banner4 = Banner(
        title="Expired Banner",
        image_url="/cdn/bexpired.jpg",
        link="/linkexpired",
        priority=2,
        is_active=True,
        start_at=now - timedelta(days=5),
        end_at=now - timedelta(days=1)
    )
    # 5. Future banner
    banner5 = Banner(
        title="Future Banner",
        image_url="/cdn/bfuture.jpg",
        link="/linkfuture",
        priority=3,
        is_active=True,
        start_at=now + timedelta(days=1),
        end_at=now + timedelta(days=5)
    )
    
    test_db.add_all([banner1, banner2, banner3, banner4, banner5])
    await test_db.commit()

    response = await client.get("/api/v1/catalog/banners")
    assert response.status_code == 200
    data = response.json()
    
    assert len(data) == 2
    # Verify sorting by priority asc
    assert data[0]["title"] == "Banner 5"
    assert data[0]["ordering"] == 5
    assert data[1]["title"] == "Banner 10"
    assert data[1]["ordering"] == 10


@pytest.mark.asyncio
async def test_no_active_banners_returns_200_empty(client: AsyncClient, test_db):
    """
    unhappy: no_active_banners_returns_200_empty
    No active banners -> returns 200 with empty list.
    """
    response = await client.get("/api/v1/catalog/banners")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_click_on_unknown_banner_returns_400(client: AsyncClient, test_db):
    """
    unhappy: click_on_unknown_banner_returns_400
    Event with non-existent banner ID -> 400.
    """
    unknown_id = str(uuid.uuid4())
    payload = {
        "events": [
            {
                "banner_id": unknown_id,
                "event": "click",
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        ]
    }
    
    response = await client.post("/api/v1/catalog/banners/events", json=payload)
    assert response.status_code == 400
    data = response.json()
    assert data["code"] == "BANNER_NOT_FOUND"


@pytest.mark.asyncio
async def test_empty_events_returns_400(client: AsyncClient, test_db):
    """
    unhappy: empty_events_returns_400
    Empty events list -> 400.
    """
    # Create an active banner to ensure we don't fail on Banner validation
    banner = Banner(
        title="Test",
        image_url="/cdn/t.jpg",
        link="/link",
        priority=1,
        is_active=True
    )
    test_db.add(banner)
    await test_db.commit()

    payload = {
        "events": []
    }
    
    response = await client.post("/api/v1/catalog/banners/events", json=payload)
    assert response.status_code == 400
    data = response.json()
    assert data["code"] == "EMPTY_EVENTS"


@pytest.mark.asyncio
async def test_post_banner_events_registered_successfully(client: AsyncClient, test_db):
    """
    happy: post_banner_events_registered_successfully
    Events are logged for both authenticated and guest users.
    """
    banner = Banner(
        title="CTR Banner",
        image_url="/cdn/ctr.jpg",
        link="/ctr-link",
        priority=1,
        is_active=True
    )
    test_db.add(banner)
    await test_db.commit()

    timestamp = datetime.now(timezone.utc).replace(tzinfo=None)

    # 1. Post as guest
    guest_payload = {
        "events": [
            {
                "banner_id": str(banner.id),
                "event": "impression",
                "timestamp": timestamp.isoformat()
            }
        ]
    }
    resp1 = await client.post("/api/v1/catalog/banners/events", json=guest_payload)
    assert resp1.status_code == 201
    
    # Verify in DB
    stmt1 = select(BannerEvent).where(BannerEvent.banner_id == banner.id, BannerEvent.event == "impression")
    res1 = await test_db.execute(stmt1)
    event1 = res1.scalars().first()
    assert event1 is not None
    assert event1.user_id is None

    # 2. Post as authenticated user
    token = generate_token(USER_ID)
    auth_payload = {
        "events": [
            {
                "banner_id": str(banner.id),
                "event": "click",
                "timestamp": timestamp.isoformat()
            }
        ]
    }
    resp2 = await client.post(
        "/api/v1/catalog/banners/events",
        json=auth_payload,
        headers={"Authorization": f"Bearer {token}"}
    )
    assert resp2.status_code == 201

    # Verify in DB
    stmt2 = select(BannerEvent).where(BannerEvent.banner_id == banner.id, BannerEvent.event == "click")
    res2 = await test_db.execute(stmt2)
    event2 = res2.scalars().first()
    assert event2 is not None
    assert event2.user_id == USER_ID


@pytest.mark.asyncio
async def test_create_banner_successfully(client: AsyncClient, test_db):
    """
    happy: test_create_banner_successfully
    Create a new banner via POST /api/v1/catalog/banners.
    """
    payload = {
        "title": "New Promo Banner",
        "image_url": "/cdn/promo.jpg",
        "link": "/catalog/promo",
        "priority": 15,
        "is_active": True,
        "start_at": "2026-06-04T12:00:00",
        "end_at": "2026-06-05T12:00:00"
    }
    
    response = await client.post("/api/v1/catalog/banners", json=payload)
    assert response.status_code == 201
    data = response.json()
    
    assert data["title"] == "New Promo Banner"
    assert data["image_url"] == "/cdn/promo.jpg"
    assert data["link"] == "/catalog/promo"
    assert data["ordering"] == 15
    assert data["active_from"] == "2026-06-04T12:00:00"
    assert data["active_to"] == "2026-06-05T12:00:00"
    
    # Verify DB contains the new banner
    stmt = select(Banner).where(Banner.id == uuid.UUID(data["id"]))
    res = await test_db.execute(stmt)
    banner_in_db = res.scalars().first()
    assert banner_in_db is not None
    assert banner_in_db.title == "New Promo Banner"

