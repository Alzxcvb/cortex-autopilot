"""
Hackathon Backend — local-first FastAPI app.

Single-tenant, single-store, SQLite, no auth.
Start with: uvicorn backend.app.main:app --reload --port 8000
"""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, func

from app.config import get_settings
from app.database import init_db, async_session_factory
from app.models import Product
from app.shopify import ShopifyClient
from app.sync import sync_all
from app.simulator import run_simulator
from app.routers import (
    store,
    products,
    orders,
    customers,
    inventory,
    analytics,
    events,
    actions,
    shopify_proxy,
    autopilot,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: init DB, auto-sync if empty, start simulator."""
    # Init database tables
    await init_db()
    logger.info("Database initialized")

    # Create Shopify client
    settings = get_settings()
    client = ShopifyClient(
        store_url=settings.SHOPIFY_STORE_URL,
        access_token=settings.SHOPIFY_ACCESS_TOKEN,
        api_version=settings.SHOPIFY_API_VERSION,
    )
    app.state.shopify = client

    # Auto-sync if DB is empty
    async with async_session_factory() as db:
        result = await db.execute(select(func.count()).select_from(Product))
        count = result.scalar()
        if count == 0:
            logger.info("Empty database — syncing from Shopify...")
            try:
                await sync_all(db, client)
                await db.commit()
                logger.info("Initial sync complete")
            except Exception as exc:
                logger.error("Initial sync failed: %s", exc)
                await db.rollback()

    # Start order simulator
    simulator_task = None
    if settings.SIMULATOR_ENABLED:
        simulator_task = asyncio.create_task(
            run_simulator(client, async_session_factory)
        )
        logger.info("Order simulator started")

    logger.info("Backend ready — %s", settings.SHOPIFY_STORE_URL)

    yield

    # Shutdown
    if simulator_task and not simulator_task.done():
        simulator_task.cancel()
        try:
            await simulator_task
        except asyncio.CancelledError:
            pass
    await client.client.aclose()
    logger.info("Shutdown complete")


app = FastAPI(
    title="Hackathon Backend",
    description="Local-first Shopify backend for hackathon teams",
    version="2.0.0",
    lifespan=lifespan,
)

# CORS — wide open for localhost dev
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount all routers
app.include_router(store.router)
app.include_router(products.router)
app.include_router(orders.router)
app.include_router(customers.router)
app.include_router(inventory.router)
app.include_router(analytics.router)
app.include_router(events.router)
app.include_router(actions.router)
app.include_router(shopify_proxy.router)
app.include_router(autopilot.router)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok", "service": "hackathon-backend", "version": "2.0.0"}


@app.get("/")
async def root():
    """Root endpoint — same as health check."""
    return {"status": "ok", "service": "hackathon-backend", "version": "2.0.0"}
