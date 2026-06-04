from pydantic import BaseModel, Field
from uuid import UUID
from typing import List, Optional, Union
from datetime import datetime
from src.modules.catalog.schemas import ImageRef

class CartItemAddRequest(BaseModel):
    sku_id: UUID
    quantity: int = Field(..., ge=1)

class CartItemUpdateRequest(BaseModel):
    quantity: int = Field(..., ge=1)

class CartItem(BaseModel):
    sku_id: UUID
    product_id: UUID
    name: str
    sku_code: Optional[str] = ""
    quantity: int = Field(..., ge=1)
    unit_price: int
    unit_price_at_add: Optional[int] = None
    line_total: int
    available_quantity: int = Field(..., ge=0)
    is_available: bool
    image: Optional[ImageRef] = None

class CartResponse(BaseModel):
    id: Optional[UUID] = None
    items: List[CartItem]
    items_count: int
    subtotal: int
    is_valid: bool
    updated_at: Optional[datetime] = None

class CartValidationIssue(BaseModel):
    sku_id: UUID
    type: str  # PRICE_CHANGED, OUT_OF_STOCK, QUANTITY_REDUCED, PRODUCT_BLOCKED, PRODUCT_DELETED
    message: str
    old_value: Optional[Union[str, int]] = None
    new_value: Optional[Union[str, int]] = None

class CartValidationResponse(BaseModel):
    is_valid: bool
    cart: CartResponse
    issues: List[CartValidationIssue]
