import uuid
import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ProductNotFound, B2BServiceUnavailable, SubscriptionAlreadyExists
from src.modules.catalog.service import CatalogService
from src.modules.favorites.models import Favorite, ProductSubscription

class FavoritesService:
    @staticmethod
    async def subscribe_to_product(db: AsyncSession, user_id: uuid.UUID, product_id: uuid.UUID, events: list[str]) -> ProductSubscription:
        """
        Subscribes a user to product notification events.
        1. Validates product existence in B2B.
        2. Raises ProductNotFound if product not found.
        3. Raises B2BServiceUnavailable if B2B is down.
        4. Checks if subscription already exists (raises SubscriptionAlreadyExists).
        5. Saves subscription with events in notify_on column.
        """
        # 1. Validate product existence in B2B
        async with await CatalogService.get_b2b_client() as client:
            try:
                resp = await client.get(f"/api/v1/public/products/{product_id}")
                if resp.status_code == 404:
                    raise ProductNotFound()
                resp.raise_for_status()
            except ProductNotFound:
                raise
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    raise ProductNotFound()
                raise B2BServiceUnavailable()
            except Exception:
                raise B2BServiceUnavailable()

        # 2. Check if already exists in DB
        stmt = select(ProductSubscription).where(
            ProductSubscription.user_id == user_id,
            ProductSubscription.product_id == product_id
        )
        result = await db.execute(stmt)
        subscription = result.scalars().first()
        if subscription:
            raise SubscriptionAlreadyExists()

        # 3. Create new subscription
        subscription = ProductSubscription(
            user_id=user_id,
            product_id=product_id,
            notify_on=events
        )
        db.add(subscription)
        await db.commit()
        await db.refresh(subscription)
        return subscription

    @staticmethod
    async def unsubscribe_from_product(db: AsyncSession, user_id: uuid.UUID, product_id: uuid.UUID) -> bool:
        """
        Unsubscribes a user from product notifications (idempotent).
        Returns True if deleted, False if did not exist.
        """
        stmt = select(ProductSubscription).where(
            ProductSubscription.user_id == user_id,
            ProductSubscription.product_id == product_id
        )
        result = await db.execute(stmt)
        subscription = result.scalars().first()
        if subscription:
            await db.delete(subscription)
            await db.commit()
            return True
        return False

    @staticmethod
    async def add_to_favorites(db: AsyncSession, user_id: uuid.UUID, product_id: uuid.UUID) -> tuple[Favorite, bool]:
        """
        Adds a product to user's favorites.
        First validates product existence in B2B.
        If product does not exist, raises ProductNotFound.
        If B2B is unavailable, raises B2BServiceUnavailable.
        Returns the favorite and a boolean indicating if it was newly created.
        """
        # 1. Validate product existence in B2B
        async with await CatalogService.get_b2b_client() as client:
            try:
                resp = await client.get(f"/api/v1/public/products/{product_id}")
                if resp.status_code == 404:
                    raise ProductNotFound()
                resp.raise_for_status()
            except ProductNotFound:
                raise
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    raise ProductNotFound()
                raise B2BServiceUnavailable()
            except Exception:
                raise B2BServiceUnavailable()

        # 2. Check if already exists in DB
        stmt = select(Favorite).where(Favorite.user_id == user_id, Favorite.product_id == product_id)
        result = await db.execute(stmt)
        favorite = result.scalars().first()
        if favorite:
            return favorite, False

        # 3. Create new favorite entry
        favorite = Favorite(user_id=user_id, product_id=product_id)
        db.add(favorite)
        await db.commit()
        await db.refresh(favorite)
        return favorite, True

    @staticmethod
    async def remove_from_favorites(db: AsyncSession, user_id: uuid.UUID, product_id: uuid.UUID) -> bool:
        """
        Removes a product from user's favorites (idempotent).
        Returns True if deleted, False if did not exist.
        """
        stmt = select(Favorite).where(Favorite.user_id == user_id, Favorite.product_id == product_id)
        result = await db.execute(stmt)
        favorite = result.scalars().first()
        if favorite:
            await db.delete(favorite)
            await db.commit()
            return True
        return False

    @staticmethod
    async def get_favorites(db: AsyncSession, user_id: uuid.UUID, limit: int = 20, offset: int = 0) -> dict:
        """
        Retrieves user's favorites with details enriched from B2B.
        Excludes deleted/blocked products that are not returned by B2B.
        Returns paginated catalog products list.
        """
        # 1. Fetch favorites from DB sorted by added_at desc
        stmt = select(Favorite).where(Favorite.user_id == user_id).order_by(Favorite.added_at.desc())
        result = await db.execute(stmt)
        favorites = result.scalars().all()

        if not favorites:
            return {
                "items": [],
                "total_count": 0,
                "total": 0,
                "limit": limit,
                "offset": offset
            }

        # 2. Batch fetch details from B2B
        product_ids = [str(f.product_id) for f in favorites]
        async with await CatalogService.get_b2b_client() as client:
            try:
                # We can call POST /api/v1/public/products/batch
                resp = await client.post("/api/v1/public/products/batch", json={"product_ids": product_ids})
                if resp.status_code == 404:
                    b2b_products = []
                else:
                    resp.raise_for_status()
                    b2b_products = resp.json()
            except httpx.HTTPStatusError:
                raise B2BServiceUnavailable()
            except Exception:
                raise B2BServiceUnavailable()

        # 3. Map and filter (excluding deleted/blocked products)
        b2c_products_mapped = {}
        for p in b2b_products:
            try:
                mapped_p = CatalogService._map_product_to_b2c(p)
                b2c_products_mapped[mapped_p["id"]] = mapped_p
            except Exception:
                continue

        # Keep original sorting by added_at
        enriched_items = []
        for f in favorites:
            f_id_str = str(f.product_id)
            if f_id_str in b2c_products_mapped:
                enriched_items.append(b2c_products_mapped[f_id_str])

        # 4. Paginate in-memory
        total_count = len(enriched_items)
        paginated_items = enriched_items[offset : offset + limit]

        return {
            "items": paginated_items,
            "total_count": total_count,
            "total": total_count,
            "limit": limit,
            "offset": offset
        }
