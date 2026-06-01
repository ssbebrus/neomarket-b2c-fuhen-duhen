from pydantic import BaseModel, UUID4
from typing import List, Optional, Dict, Any

class ErrorResponse(BaseModel):
    code: str
    message: str

class CategoryRef(BaseModel):
    id: UUID4
    name: str
    level: int
    path: List[str]
    parent_id: Optional[UUID4] = None

class ImageRef(BaseModel):
    id: UUID4
    url: str
    alt: str = ""
    ordering: int = 0
    is_main: bool = False

class SellerRef(BaseModel):
    id: UUID4
    display_name: str

class CatalogSku(BaseModel):
    id: UUID4
    name: str
    sku_code: str
    price: int
    old_price: Optional[int] = None
    available_quantity: int
    attributes: Dict[str, Any]
    images: List[ImageRef]

class CatalogProductCard(BaseModel):
    id: UUID4
    name: str
    slug: str
    category: CategoryRef
    min_price: int
    old_price: Optional[int] = None
    has_stock: bool
    rating: Optional[float] = None
    reviews_count: int = 0
    images: List[ImageRef]
    seller: SellerRef

class CatalogProductDetail(CatalogProductCard):
    description: str
    attributes: Dict[str, Any]
    skus: List[CatalogSku]

class CategoryTreeNode(CategoryRef):
    children: List["CategoryTreeNode"] = []

class CategoryParentRef(BaseModel):
    id: UUID4
    name: str
    slug: str

class CategorySeo(BaseModel):
    title: str
    description: str
    keywords: List[str]

class CategoryMetaTags(BaseModel):
    og_title: str
    og_description: str

class CategoryDetail(BaseModel):
    id: UUID4
    name: str
    slug: str
    description: str
    parent: Optional[CategoryParentRef] = None
    product_count: int
    seo: CategorySeo
    meta_tags: CategoryMetaTags
    image_url: str
    is_active: bool
    created_at: str
    updated_at: str

class BreadcrumbItem(BaseModel):
    id: UUID4
    slug: str
    name: str
    url: str
    level: int
    is_current: bool

class BreadcrumbsMeta(BaseModel):
    resolved_via: str
    category_id: UUID4

class BreadcrumbsResponse(BaseModel):
    data: List[BreadcrumbItem]
    meta: BreadcrumbsMeta

