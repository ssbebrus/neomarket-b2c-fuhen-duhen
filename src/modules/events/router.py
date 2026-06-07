import uuid
import logging
from typing import List, Optional
from datetime import datetime
from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.database import get_db
from src.config import settings
from src.modules.events.service import EventsService

logger = logging.getLogger("b2b_events_router")

router = APIRouter(prefix="/b2b", tags=["B2B Events"])

class B2BEventRequest(BaseModel):
    idempotency_key: uuid.UUID
    event_type: str = Field(default="")
    event: Optional[str] = None
    product_id: Optional[uuid.UUID] = None
    sku_ids: List[uuid.UUID] = Field(default_factory=list)
    reason: Optional[str] = None
    occurred_at: Optional[datetime] = None
    date: Optional[datetime] = None
    payload: Optional[dict] = None

    @model_validator(mode="before")
    @classmethod
    def normalize_fields(cls, values: dict) -> dict:
        if not isinstance(values, dict):
            return values
        
        # 1. Normalize event_type / event
        event_val = values.get("event") or values.get("event_type")
        if event_val:
            values["event"] = event_val
            values["event_type"] = event_val

        # 2. Normalize date / occurred_at
        date_val = values.get("date") or values.get("occurred_at")
        if date_val:
            values["date"] = date_val
            values["occurred_at"] = date_val

        # 3. Pull from payload if available
        payload = values.get("payload")
        if isinstance(payload, dict):
            if "product_id" in payload and not values.get("product_id"):
                values["product_id"] = payload["product_id"]
            if "reason" in payload and not values.get("reason"):
                values["reason"] = payload["reason"]
            if "sku_ids" in payload and not values.get("sku_ids"):
                values["sku_ids"] = payload["sku_ids"]
            if "sku_id" in payload and not values.get("sku_ids"):
                values["sku_ids"] = [payload["sku_id"]]

        return values

@router.post("/events")
async def receive_b2b_event(
    body: B2BEventRequest,
    x_service_key: Optional[str] = Header(None, alias="X-Service-Key"),
    db: AsyncSession = Depends(get_db)
):
    # 1. Security Check
    if not x_service_key or x_service_key != settings.B2B_TO_B2C_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Missing or invalid X-Service-Key"}
        )

    # 2. Idempotency Check
    if await EventsService.is_event_processed(db, body.idempotency_key):
        logger.info(f"Event with idempotency_key {body.idempotency_key} already processed. Returning 200 OK.")
        return {"accepted": True}

    # Resolve sku_ids if empty but product_id is provided
    resolved_sku_ids = list(body.sku_ids)
    if not resolved_sku_ids and body.product_id:
        from src.modules.catalog.service import CatalogService
        try:
            async with await CatalogService.get_b2b_client() as client:
                resp = await client.get(f"/api/v1/public/products/{body.product_id}")
                if resp.status_code == 200:
                    product_data = resp.json()
                    skus = product_data.get("skus", [])
                    for s in skus:
                        if "id" in s:
                            try:
                                resolved_sku_ids.append(uuid.UUID(s["id"]))
                            except ValueError:
                                pass
        except Exception as e:
            logger.error(f"Failed to fetch SKU IDs from B2B for product {body.product_id}: {e}")

    # 3. Process the Event
    await EventsService.process_b2b_event(db, body.event_type, resolved_sku_ids)

    # 4. Save Idempotency Key
    await EventsService.save_event_idempotency(db, body.idempotency_key)

    return {"accepted": True}
