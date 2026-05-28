from fastapi import APIRouter
from src.modules.catalog.router import router as catalog_router

api_router = APIRouter()
api_router.include_router(catalog_router, tags=["Catalog"])
