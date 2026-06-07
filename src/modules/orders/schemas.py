import uuid
from datetime import datetime, timezone
from typing import List, Optional
from pydantic import BaseModel, Field, UUID4, ConfigDict

class OrderItemSnapshot(BaseModel):
    sku_id: UUID4
    quantity: int = Field(..., ge=1)
    unit_price: int = Field(..., ge=0)

class OrderCreateRequest(BaseModel):
    address_id: UUID4
    payment_method_id: UUID4
    comment: Optional[str] = Field(None, max_length=1000)
    items_snapshot: Optional[List[OrderItemSnapshot]] = None

class StatusHistoryItem(BaseModel):
    status: str
    changed_at: datetime
    reason: Optional[str] = None

class OrderItemResponse(BaseModel):
    sku_id: UUID4
    product_id: UUID4
    name: str
    sku_code: str
    quantity: int
    unit_price: int
    line_total: int
    image_url: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)

class AddressResponse(BaseModel):
    id: UUID4
    country: str = "Россия"
    region: Optional[str] = None
    city: str = "Москва"
    street: str = "Ленина"
    building: str = "1"
    apartment: Optional[str] = None
    postal_code: Optional[str] = None
    recipient_name: Optional[str] = None
    recipient_phone: Optional[str] = None
    is_default: bool = False
    comment: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

class PaymentMethodResponse(BaseModel):
    id: UUID4
    type: str = "CARD"
    card_last4: Optional[str] = "4321"
    card_brand: Optional[str] = "MIR"
    is_default: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

class OrderResponse(BaseModel):
    id: UUID4
    number: str
    buyer_id: UUID4
    status: str
    status_history: Optional[List[StatusHistoryItem]] = None
    items: List[OrderItemResponse]
    subtotal: int
    delivery_cost: int = 0
    total: int
    address: AddressResponse
    payment_method: PaymentMethodResponse
    comment: Optional[str] = None
    cancel_reason: Optional[str] = None
    created_at: datetime
    paid_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)

class PaginatedOrders(BaseModel):
    items: List[OrderResponse]
    total_count: int
    limit: int
    offset: int


class OrderCancelRequest(BaseModel):
    reason: Optional[str] = Field(None, max_length=500)

