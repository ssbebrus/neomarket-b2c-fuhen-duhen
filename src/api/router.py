from fastapi import APIRouter
from src.modules.catalog.router import router as catalog_router
from src.modules.favorites.router import router as favorites_router
from src.modules.cart.router import router as cart_router

api_router = APIRouter()
api_router.include_router(catalog_router, tags=["Catalog"])
api_router.include_router(favorites_router, tags=["Favorites"])
api_router.include_router(cart_router, tags=["Cart"])
