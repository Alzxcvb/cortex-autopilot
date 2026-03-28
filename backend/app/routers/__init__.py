"""Router package — all FastAPI endpoint modules."""
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

__all__ = [
    "store",
    "products",
    "orders",
    "customers",
    "inventory",
    "analytics",
    "events",
    "actions",
    "shopify_proxy",
    "autopilot",
]
