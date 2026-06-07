from src.modules.catalog.schemas import ImageRef
import uuid
import httpx
import asyncio
from datetime import datetime, timezone
from typing import Optional, Tuple, List, Dict, Any
from sqlalchemy import select, delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import B2BServiceUnavailable, SKUNotFound, ProductUnavailable, CartItemNotFound, InsufficientStock
from src.modules.catalog.service import CatalogService
from src.modules.cart.models import CartItem as CartItemModel
from src.modules.cart.schemas import (
    CartItem as CartItemSchema,
    CartResponse,
    CartValidationIssue,
    CartValidationResponse
)

class CartService:
    @staticmethod
    async def get_b2b_sku(client: httpx.AsyncClient, sku_id: uuid.UUID) -> Optional[dict]:
        try:
            resp = await client.get(f"/api/v1/public/skus/{sku_id}")
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise B2BServiceUnavailable()
        except Exception:
            raise B2BServiceUnavailable()

    @staticmethod
    async def get_b2b_product_service(client: httpx.AsyncClient, product_id: uuid.UUID) -> Optional[dict]:
        try:
            resp = await client.get(f"/api/v1/products/{product_id}")
            if resp.status_code == 200:
                return resp.json()
            return None
        except Exception:
            return None

    @classmethod
    async def enrich_cart_items(
        cls,
        db: AsyncSession,
        user_id: Optional[uuid.UUID],
        session_id: Optional[str]
    ) -> Tuple[List[CartItemSchema], bool, int, int]:
        """
        Enriches DB cart items with live data from B2B.
        Returns:
            - List of enriched CartItem schemas
            - is_valid (bool): true if all items are available and quantities <= stock
            - items_count (int): total quantity of all items in cart
            - subtotal (int): sum of line totals (unavailable items count as 0)
        """
        # 1. Fetch cart items from DB
        if user_id:
            stmt = select(CartItemModel).where(CartItemModel.user_id == user_id)
        else:
            stmt = select(CartItemModel).where(CartItemModel.session_id == session_id)
        
        result = await db.execute(stmt)
        db_items = result.scalars().all()

        if not db_items:
            return [], True, 0, 0

        # 2. Query SKU details in parallel from B2B
        async with await CatalogService.get_b2b_client() as client:
            tasks = [cls.get_b2b_sku(client, item.sku_id) for item in db_items]
            skus_data = await asyncio.gather(*tasks)

        sku_map = {db_items[i].sku_id: skus_data[i] for i in range(len(db_items))}

        # 3. Collect product IDs from successfully retrieved SKUs
        product_ids = set()
        for sku_val in sku_map.values():
            if sku_val and "product_id" in sku_val:
                product_ids.add(sku_val["product_id"])

        # 4. Fetch product details in batch
        products_batch = []
        if product_ids:
            async with await CatalogService.get_b2b_client() as client:
                try:
                    resp = await client.post(
                        "/api/v1/public/products/batch",
                        json={"product_ids": [str(pid) for pid in product_ids]}
                    )
                    if resp.status_code != 404:
                        resp.raise_for_status()
                        products_batch = resp.json()
                except Exception:
                    raise B2BServiceUnavailable()

        product_map = {p["id"]: p for p in products_batch}

        # 5. Fetch details for missing/blocked products via Service endpoint (X-Service-Key)
        missing_product_ids = product_ids - set(product_map.keys())
        if missing_product_ids:
            async with await CatalogService.get_b2b_client() as client:
                service_tasks = [cls.get_b2b_product_service(client, uuid.UUID(pid)) for pid in missing_product_ids]
                service_products = await asyncio.gather(*service_tasks)
                for sp in service_products:
                    if sp and "id" in sp:
                        product_map[sp["id"]] = sp

        # 6. Map to B2C CartItem Response schemas
        enriched_items = []
        is_cart_valid = True
        total_items_count = 0
        total_subtotal = 0

        for item in db_items:
            sku = sku_map.get(item.sku_id)
            total_items_count += item.quantity

            if not sku:
                raise SKUNotFound()

            product_id_str = sku.get("product_id")
            product = product_map.get(product_id_str) if product_id_str else None

            # Determine product info and availability status
            product_title = "Unknown Product"
            is_product_active = False
            is_blocked = False
            is_deleted = False
            is_on_moderation = False

            if product:
                product_title = product.get("title", "Unknown Product")
                deleted_flag = product.get("deleted", False)
                status = product.get("status", "CREATED")

                if deleted_flag:
                    is_deleted = True
                elif status in ("BLOCKED", "HARD_BLOCKED"):
                    is_blocked = True
                elif status in ("ON_MODERATION", "CREATED"):
                    is_on_moderation = True
                elif status == "MODERATED":
                    is_product_active = True
            else:
                is_deleted = True

            sku_name = sku.get("name") or ""
            sku_title = f"{product_title} {sku_name}".strip()
            sku_code = sku.get("article") or ""
            
            # Pricing
            price = sku.get("price", 0)
            discount = sku.get("discount", 0)
            current_unit_price = max(0, price - discount)

            active_stock = sku.get("active_quantity", 0)

            # Determine if SKU is available
            is_available = True
            if is_deleted or is_blocked or is_on_moderation or item.unavailable_reason:
                is_available = False
            elif active_stock <= 0:
                is_available = False

            # Check if this item is valid (available and enough stock)
            item_is_valid = is_available and (item.quantity <= active_stock)
            if not item_is_valid:
                is_cart_valid = False

            # Calculate line total
            line_total = current_unit_price * item.quantity if is_available else 0
            if is_available:
                total_subtotal += line_total

            # Format image
            image_ref = None
            sku_images = sku.get("images", [])
            product_images = product.get("images", []) if product else []

            img_data = None
            if sku_images:
                # Find ordering = 0 or first image
                sku_images_sorted = sorted(sku_images, key=lambda img: img.get("ordering", 999))
                img_data = sku_images_sorted[0]
            elif product_images:
                # Find ordering = 0 or first image
                product_images_sorted = sorted(product_images, key=lambda img: img.get("ordering", 999))
                img_data = product_images_sorted[0]

            if img_data:
                image_ref = ImageRef(
                    id=img_data.get("id") or uuid.uuid4(),
                    url=img_data.get("url", ""),
                    alt=img_data.get("alt", ""),
                    ordering=img_data.get("ordering", 0),
                    is_main=img_data.get("ordering", 0) == 0
                )

            enriched_items.append(
                CartItemSchema(
                    sku_id=item.sku_id,
                    product_id=uuid.UUID(product_id_str) if product_id_str else uuid.UUID("00000000-0000-0000-0000-000000000000"),
                    name=sku_title,
                    sku_code=sku_code,
                    quantity=item.quantity,
                    unit_price=current_unit_price,
                    unit_price_at_add=item.unit_price_at_add,
                    line_total=line_total,
                    available_quantity=active_stock,
                    is_available=is_available,
                    unavailable_reason=item.unavailable_reason,
                    image=image_ref
                )
            )

        return enriched_items, is_cart_valid, total_items_count, total_subtotal

    @classmethod
    async def get_cart(
        cls,
        db: AsyncSession,
        user_id: Optional[uuid.UUID],
        session_id: Optional[str]
    ) -> CartResponse:
        """
        Retrieves enriched cart state.
        """
        enriched_items, is_valid, items_count, subtotal = await cls.enrich_cart_items(db, user_id, session_id)
        
        # ID is either user_id or session_id (if valid UUID, else None)
        cart_id = None
        if user_id:
            cart_id = user_id
        elif session_id:
            try:
                cart_id = uuid.UUID(session_id)
            except ValueError:
                pass

        return CartResponse(
            id=cart_id,
            items=enriched_items,
            items_count=items_count,
            subtotal=subtotal,
            is_valid=is_valid,
            updated_at=datetime.now(timezone.utc).replace(tzinfo=None)
        )

    @classmethod
    async def add_item(
        cls,
        db: AsyncSession,
        user_id: Optional[uuid.UUID],
        session_id: Optional[str],
        sku_id: uuid.UUID,
        quantity: int
    ) -> CartResponse:
        """
        Adds SKU to cart.
        Validates SKU/product in B2B. If not exists or blocked/deleted, raises HTTPException 404.
        If stock insufficient, raises HTTPException 409.
        """
        # 1. Fetch SKU details from B2B
        async with await CatalogService.get_b2b_client() as client:
            sku = await cls.get_b2b_sku(client, sku_id)
            if not sku:
                raise SKUNotFound()

            # Get parent product info
            product_id_str = sku.get("product_id")
            if not product_id_str:
                raise SKUNotFound()

            # Query product via service key to check active/blocked/deleted status
            product = await cls.get_b2b_product_service(client, uuid.UUID(product_id_str))
            if not product or product.get("deleted", False) or product.get("status") in ("BLOCKED", "HARD_BLOCKED"):
                raise ProductUnavailable()

        # Calculate unit price
        price = sku.get("price", 0)
        discount = sku.get("discount", 0)
        current_unit_price = max(0, price - discount)
        active_stock = sku.get("active_quantity", 0)

        # 2. Check if item already exists in DB
        if user_id:
            stmt = select(CartItemModel).where(CartItemModel.user_id == user_id, CartItemModel.sku_id == sku_id)
        else:
            stmt = select(CartItemModel).where(CartItemModel.session_id == session_id, CartItemModel.sku_id == sku_id)
        
        result = await db.execute(stmt)
        existing_item = result.scalars().first()

        new_quantity = quantity
        if existing_item:
            new_quantity += existing_item.quantity

        # Check stock limits
        if new_quantity > active_stock:
            raise InsufficientStock()

        # 3. Create or update item in DB
        if existing_item:
            existing_item.quantity = new_quantity
            existing_item.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        else:
            new_item = CartItemModel(
                user_id=user_id,
                session_id=session_id,
                sku_id=sku_id,
                quantity=quantity,
                unit_price_at_add=current_unit_price
            )
            db.add(new_item)

        await db.commit()
        return await cls.get_cart(db, user_id, session_id)

    @classmethod
    async def update_item_quantity(
        cls,
        db: AsyncSession,
        user_id: Optional[uuid.UUID],
        session_id: Optional[str],
        sku_id: uuid.UUID,
        quantity: int
    ) -> CartResponse:
        """
        Updates SKU quantity in cart.
        """
        # 1. Fetch item from DB
        if user_id:
            stmt = select(CartItemModel).where(CartItemModel.user_id == user_id, CartItemModel.sku_id == sku_id)
        else:
            stmt = select(CartItemModel).where(CartItemModel.session_id == session_id, CartItemModel.sku_id == sku_id)

        result = await db.execute(stmt)
        existing_item = result.scalars().first()

        if not existing_item:
            raise CartItemNotFound()

        # 2. Check stock limits in B2B
        async with await CatalogService.get_b2b_client() as client:
            sku = await cls.get_b2b_sku(client, sku_id)
            if not sku:
                raise SKUNotFound()
            active_stock = sku.get("active_quantity", 0)

        if quantity > active_stock:
            raise InsufficientStock()

        # 3. Update quantity
        existing_item.quantity = quantity
        existing_item.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await db.commit()

        return await cls.get_cart(db, user_id, session_id)

    @classmethod
    async def delete_item(
        cls,
        db: AsyncSession,
        user_id: Optional[uuid.UUID],
        session_id: Optional[str],
        sku_id: uuid.UUID
    ) -> CartResponse:
        """
        Deletes item from cart.
        """
        if user_id:
            stmt = delete(CartItemModel).where(CartItemModel.user_id == user_id, CartItemModel.sku_id == sku_id)
        else:
            stmt = delete(CartItemModel).where(CartItemModel.session_id == session_id, CartItemModel.sku_id == sku_id)

        await db.execute(stmt)
        await db.commit()

        return await cls.get_cart(db, user_id, session_id)

    @classmethod
    async def clear_cart(
        cls,
        db: AsyncSession,
        user_id: Optional[uuid.UUID],
        session_id: Optional[str]
    ) -> None:
        """
        Clears cart entirely.
        """
        if user_id:
            stmt = delete(CartItemModel).where(CartItemModel.user_id == user_id)
        else:
            stmt = delete(CartItemModel).where(CartItemModel.session_id == session_id)

        await db.execute(stmt)
        await db.commit()

    @classmethod
    async def merge_carts(
        cls,
        db: AsyncSession,
        user_id: uuid.UUID,
        session_id: str
    ) -> CartResponse:
        """
        Merges guest cart items (session_id) into user's cart (user_id) based on max quantity.
        """
        # Fetch guest items
        guest_stmt = select(CartItemModel).where(CartItemModel.session_id == session_id)
        guest_result = await db.execute(guest_stmt)
        guest_items = guest_result.scalars().all()

        if not guest_items:
            return await cls.get_cart(db, user_id, None)

        # Fetch user items
        user_stmt = select(CartItemModel).where(CartItemModel.user_id == user_id)
        user_result = await db.execute(user_stmt)
        user_items = user_result.scalars().all()

        user_item_map = {item.sku_id: item for item in user_items}

        for guest_item in guest_items:
            user_item = user_item_map.get(guest_item.sku_id)
            if user_item:
                # Merge: max(guest, auth)
                user_item.quantity = max(user_item.quantity, guest_item.quantity)
                user_item.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
                # Delete guest item
                await db.delete(guest_item)
            else:
                # Transfer ownership: set user_id, clear session_id
                guest_item.user_id = user_id
                guest_item.session_id = None
                guest_item.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)

        await db.commit()
        return await cls.get_cart(db, user_id, None)

    @classmethod
    async def validate_cart(
        cls,
        db: AsyncSession,
        user_id: Optional[uuid.UUID],
        session_id: Optional[str]
    ) -> CartValidationResponse:
        """
        Validates cart details against live B2B. Collects all issues.
        """
        # 1. Fetch current cart state
        # In order to validate prices, we need the DB cart items unit_price_at_add
        if user_id:
            stmt = select(CartItemModel).where(CartItemModel.user_id == user_id)
        else:
            stmt = select(CartItemModel).where(CartItemModel.session_id == session_id)

        result = await db.execute(stmt)
        db_items = result.scalars().all()
        db_item_map = {item.sku_id: item for item in db_items}

        # 2. Enrich items
        enriched_items, is_valid, items_count, subtotal = await cls.enrich_cart_items(db, user_id, session_id)

        # Construct CartResponse
        cart_id = None
        if user_id:
            cart_id = user_id
        elif session_id:
            try:
                cart_id = uuid.UUID(session_id)
            except ValueError:
                pass

        cart_resp = CartResponse(
            id=cart_id,
            items=enriched_items,
            items_count=items_count,
            subtotal=subtotal,
            is_valid=is_valid,
            updated_at=datetime.now(timezone.utc).replace(tzinfo=None)
        )

        # 3. Retrieve B2B details for each unique item to determine precise validation issues
        issues = []

        async with await CatalogService.get_b2b_client() as client:
            for item in enriched_items:
                db_item = db_item_map.get(item.sku_id)
                if not db_item:
                    continue

                sku = await cls.get_b2b_sku(client, item.sku_id)
                if not sku:
                    issues.append(
                        CartValidationIssue(
                            sku_id=item.sku_id,
                            type="PRODUCT_DELETED",
                            message="Product was deleted"
                        )
                    )
                    continue

                product_id_str = sku.get("product_id")
                product = None
                if product_id_str:
                    product = await cls.get_b2b_product_service(client, uuid.UUID(product_id_str))

                if not product or product.get("deleted", False):
                    issues.append(
                        CartValidationIssue(
                            sku_id=item.sku_id,
                            type="PRODUCT_DELETED",
                            message="Product was deleted"
                        )
                    )
                    continue

                status = product.get("status", "CREATED")
                if status in ("BLOCKED", "HARD_BLOCKED"):
                    issues.append(
                        CartValidationIssue(
                            sku_id=item.sku_id,
                            type="PRODUCT_BLOCKED",
                            message="Product was blocked"
                        )
                    )
                    continue

                if status in ("ON_MODERATION", "CREATED"):
                    issues.append(
                        CartValidationIssue(
                            sku_id=item.sku_id,
                            type="PRODUCT_BLOCKED", # Or we can use PRODUCT_BLOCKED since it's not active
                            message="Product is temporarily unavailable"
                        )
                    )
                    continue

                # Check stock quantity
                active_stock = sku.get("active_quantity", 0)
                if active_stock <= 0:
                    issues.append(
                        CartValidationIssue(
                            sku_id=item.sku_id,
                            type="OUT_OF_STOCK",
                            message="Product is out of stock"
                        )
                    )
                elif active_stock < db_item.quantity:
                    issues.append(
                        CartValidationIssue(
                            sku_id=item.sku_id,
                            type="QUANTITY_REDUCED",
                            message="Available stock is less than requested quantity",
                            old_value=db_item.quantity,
                            new_value=active_stock
                        )
                    )

                # Check price changes
                price = sku.get("price", 0)
                discount = sku.get("discount", 0)
                current_unit_price = max(0, price - discount)

                if db_item.unit_price_at_add is not None and current_unit_price != db_item.unit_price_at_add:
                    issues.append(
                        CartValidationIssue(
                            sku_id=item.sku_id,
                            type="PRICE_CHANGED",
                            message="Price has changed",
                            old_value=db_item.unit_price_at_add,
                            new_value=current_unit_price
                        )
                    )

        # The cart is valid for checkout if there are no critical issues
        # Critical issues are OUT_OF_STOCK, PRODUCT_BLOCKED, PRODUCT_DELETED, QUANTITY_REDUCED.
        # PRICE_CHANGED is just a warning, doesn't prevent checkout.
        checkout_blocking_issues = [
            issue for issue in issues 
            if issue.type in ("OUT_OF_STOCK", "PRODUCT_BLOCKED", "PRODUCT_DELETED", "QUANTITY_REDUCED")
        ]
        is_checkout_valid = len(checkout_blocking_issues) == 0 and len(enriched_items) > 0

        # Update cart response validity to reflect if checkout is valid
        cart_resp.is_valid = is_checkout_valid

        return CartValidationResponse(
            is_valid=is_checkout_valid,
            cart=cart_resp,
            issues=issues
        )
