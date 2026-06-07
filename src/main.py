import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from src.config import settings
from src.api.router import api_router
from src.modules.orders.service import OrdersService

@asynccontextmanager
async def lifespan(app: FastAPI):
    cancel_task = asyncio.create_task(OrdersService.run_cancel_pending_worker())
    fulfill_task = asyncio.create_task(OrdersService.run_fulfill_worker())
    yield
    cancel_task.cancel()
    fulfill_task.cancel()
    try:
        await asyncio.gather(cancel_task, fulfill_task, return_exceptions=True)
    except Exception:
        pass

app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    lifespan=lifespan
)

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if isinstance(exc.detail, dict):
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.detail,
        )
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": "ERROR", "message": str(exc.detail)},
    )

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = exc.errors()
    message = "Invalid request"
    if errors:
        err = errors[0]
        field = ".".join([str(loc) for loc in err["loc"] if loc != "body"])
        message = f"{field} {err['msg']}".strip()
        if "category_id" in field and "missing" in err['msg'].lower():
            message = "category_id is required"
        elif "title" in field and "missing" in err['msg'].lower():
            message = "title is required"
        elif "images" in field and "missing" in err['msg'].lower():
            message = "At least one image is required"
            
    return JSONResponse(
        status_code=400,
        content={"code": "VALIDATION_ERROR", "message": message},
    )

app.include_router(api_router, prefix=settings.API_V1_STR)

@app.get("/health")
async def health_check():
    """Лёгкий эндпоинт для проверки жизнеспособности сервиса (в докере или kubernetes)"""
    return {"status": "ok"}
