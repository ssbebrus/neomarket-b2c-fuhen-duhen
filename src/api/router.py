from fastapi import APIRouter
from src.modules.catalog.router import router as catalog_router
from src.modules.favorites.router import router as favorites_router
from src.modules.cart.router import router as cart_router
from src.modules.banners.router import router as banners_router
from src.modules.orders.router import router as orders_router
from src.modules.events.router import router as events_router

api_router = APIRouter()
api_router.include_router(catalog_router, tags=["Catalog"])
api_router.include_router(favorites_router, tags=["Favorites"])
api_router.include_router(cart_router, tags=["Cart"])
api_router.include_router(banners_router, tags=["Banners"])
api_router.include_router(orders_router, tags=["Orders"])
api_router.include_router(events_router, tags=["B2B Events"])


