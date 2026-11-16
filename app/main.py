from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import auth, users
from app.api.routes import admin, catalog, orders, shopping, sellers
from app.core.bootstrap import bootstrap_admin
from app.core.config import settings
from app.core.database import Base, SessionLocal, engine
from app import models  # pyright: ignore[reportUnusedImport]


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
    if not models.__all__:
        raise RuntimeError("No database models are registered")
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        bootstrap_admin(db)
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["Authorization", "Content-Type"],
)
app.include_router(auth.router, prefix="/api/v1")
app.include_router(users.router, prefix="/api/v1")
app.include_router(sellers.router, prefix="/api/v1")
app.include_router(catalog.router, prefix="/api/v1")
app.include_router(shopping.router, prefix="/api/v1")
app.include_router(orders.router, prefix="/api/v1")
app.include_router(admin.router, prefix="/api/v1")


@app.get("/health", tags=["health"])
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def root() -> dict[str, str]:
    return {"message": "ShopHub API is running"}
