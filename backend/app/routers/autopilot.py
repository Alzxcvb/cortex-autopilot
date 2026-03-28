"""Autopilot endpoints — rule-based AI store analysis engine."""
import logging
import math
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Customer, Order, Product

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/autopilot", tags=["autopilot"])


# ---------------------------------------------------------------------------
# Pydantic response models
# ---------------------------------------------------------------------------


class InsightType(str, Enum):
    critical = "critical"
    warning = "warning"
    opportunity = "opportunity"
    success = "success"


class InsightCategory(str, Enum):
    inventory = "inventory"
    revenue = "revenue"
    customers = "customers"
    operations = "operations"


class ImpactLevel(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"


class ActionPayload(BaseModel):
    type: str
    label: str
    params: dict[str, Any] = Field(default_factory=dict)


class Insight(BaseModel):
    id: str
    type: InsightType
    category: InsightCategory
    title: str
    description: str
    impact: ImpactLevel
    action: ActionPayload | None = None


class RevenueTrend(str, Enum):
    up = "up"
    down = "down"
    flat = "flat"


class Summary(BaseModel):
    total_products: int = 0
    total_orders_7d: int = 0
    revenue_7d: float = 0.0
    revenue_trend: RevenueTrend = RevenueTrend.flat
    revenue_change_pct: float = 0.0
    low_stock_count: int = 0
    top_product: str = ""
    active_customers_7d: int = 0
    avg_order_value: float = 0.0


class AnalyzeResponse(BaseModel):
    score: int
    insights: list[Insight]
    summary: Summary


class ExecuteActionRequest(BaseModel):
    type: str
    params: dict[str, Any] = Field(default_factory=dict)


class ExecuteActionResponse(BaseModel):
    status: str
    message: str
    result: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_date(date_str: str | None) -> datetime | None:
    if not date_str:
        return None
    try:
        cleaned = date_str.replace("Z", "+00:00")
        return datetime.fromisoformat(cleaned)
    except (ValueError, AttributeError):
        return None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _insight_id(prefix: str, seq: int) -> str:
    return f"{prefix}-{seq:03d}"


# ---------------------------------------------------------------------------
# Analysis sub-engines
# ---------------------------------------------------------------------------


def _analyze_inventory(
    products: list[Product],
    product_velocity: dict[str, float],
    product_orders_7d: dict[str, int],
    now: datetime,
) -> list[Insight]:
    """Detect low-stock, stockout risk, and slow movers."""
    insights: list[Insight] = []
    seq = 0

    for p in products:
        if p.status != "active":
            continue

        stock = p.inventory_total or 0
        velocity = product_velocity.get(p.id, 0.0)
        orders_count = product_orders_7d.get(p.id, 0)

        # Fast mover about to stock out
        if velocity > 0 and stock > 0:
            days_left = stock / velocity
            if days_left <= 3:
                seq += 1
                insights.append(Insight(
                    id=_insight_id("inv", seq),
                    type=InsightType.critical if days_left <= 1 else InsightType.warning,
                    category=InsightCategory.inventory,
                    title=f"Low stock: {p.title}",
                    description=(
                        f"Only {stock} units left. "
                        f"At current velocity ({velocity:.1f}/day), stockout in {days_left:.1f} days."
                    ),
                    impact=ImpactLevel.high,
                    action=ActionPayload(
                        type="flag",
                        label=f"Flag for reorder — {p.title}",
                        params={"product_id": p.id, "product_title": p.title, "current_stock": stock},
                    ),
                ))
                continue

        # Static low stock (< 5 units) regardless of velocity
        if 0 < stock < 5 and velocity == 0:
            seq += 1
            insights.append(Insight(
                id=_insight_id("inv", seq),
                type=InsightType.warning,
                category=InsightCategory.inventory,
                title=f"Low stock (slow mover): {p.title}",
                description=(
                    f"Only {stock} units remain with no recent sales. "
                    "Consider a discount to move remaining inventory."
                ),
                impact=ImpactLevel.medium,
                action=ActionPayload(
                    type="create_discount",
                    label=f"Create 20% discount for {p.title}",
                    params={
                        "code": f"MOVE{p.handle[:8].upper().replace('-', '')}",
                        "percentage": 20,
                    },
                ),
            ))

        # Has stock but zero orders in 7 days — pricing opportunity / stale
        if stock > 5 and orders_count == 0:
            seq += 1
            insights.append(Insight(
                id=_insight_id("inv", seq),
                type=InsightType.opportunity,
                category=InsightCategory.inventory,
                title=f"No sales in 7 days: {p.title}",
                description=(
                    f"{stock} units in stock but zero orders this week. "
                    "A targeted discount or promotion could revive demand."
                ),
                impact=ImpactLevel.medium,
                action=ActionPayload(
                    type="create_discount",
                    label=f"Create 15% discount for {p.title}",
                    params={
                        "code": f"REVIVE{p.handle[:6].upper().replace('-', '')}",
                        "percentage": 15,
                    },
                ),
            ))

    return insights


def _analyze_revenue(
    orders_7d: list[Order],
    orders_prev_7d: list[Order],
) -> tuple[list[Insight], RevenueTrend, float]:
    """Compare last 7d vs previous 7d revenue."""
    insights: list[Insight] = []

    rev_7d = sum(o.total_price for o in orders_7d)
    rev_prev = sum(o.total_price for o in orders_prev_7d)

    if rev_prev > 0:
        change_pct = ((rev_7d - rev_prev) / rev_prev) * 100
    elif rev_7d > 0:
        change_pct = 100.0
    else:
        change_pct = 0.0

    if change_pct > 5:
        trend = RevenueTrend.up
    elif change_pct < -5:
        trend = RevenueTrend.down
    else:
        trend = RevenueTrend.flat

    if change_pct < -10:
        insights.append(Insight(
            id="rev-001",
            type=InsightType.warning,
            category=InsightCategory.revenue,
            title="Revenue declining",
            description=(
                f"Revenue is down {abs(change_pct):.1f}% vs the previous 7 days "
                f"(${rev_7d:,.2f} vs ${rev_prev:,.2f})."
            ),
            impact=ImpactLevel.high,
            action=ActionPayload(
                type="create_discount",
                label="Create store-wide 10% flash sale",
                params={"code": "FLASH10", "percentage": 10},
            ),
        ))
    elif change_pct > 15:
        insights.append(Insight(
            id="rev-001",
            type=InsightType.success,
            category=InsightCategory.revenue,
            title="Revenue surging",
            description=(
                f"Revenue is up {change_pct:.1f}% vs the previous 7 days "
                f"(${rev_7d:,.2f} vs ${rev_prev:,.2f}). Keep it going!"
            ),
            impact=ImpactLevel.low,
            action=None,
        ))

    return insights, trend, round(change_pct, 1)


def _analyze_customers(
    customers: list[Customer],
    orders_7d: list[Order],
    now: datetime,
) -> list[Insight]:
    """Identify VIPs, at-risk customers, and new customer trends."""
    insights: list[Insight] = []

    if not customers:
        return insights

    # VIP detection — top 10% by spend
    sorted_by_spend = sorted(customers, key=lambda c: c.total_spent, reverse=True)
    vip_cutoff = max(1, len(sorted_by_spend) // 10)
    vips = sorted_by_spend[:vip_cutoff]

    if vips:
        top_vip = vips[0]
        insights.append(Insight(
            id="cust-001",
            type=InsightType.success,
            category=InsightCategory.customers,
            title=f"Top VIP: {top_vip.first_name} {top_vip.last_name}",
            description=(
                f"{vip_cutoff} VIP customer(s) identified (top 10% by spend). "
                f"Top spender: ${top_vip.total_spent:,.2f} across {top_vip.orders_count} orders."
            ),
            impact=ImpactLevel.medium,
            action=ActionPayload(
                type="send_email",
                label=f"Send VIP thank-you to {top_vip.email}",
                params={"to": top_vip.email, "subject": "Thank you for being a valued customer!"},
            ),
        ))

    # At-risk customers — ordered before but nothing in 14+ days
    at_risk: list[Customer] = []
    for c in customers:
        if c.orders_count > 0 and c.last_order_at:
            last_order_dt = _parse_date(c.last_order_at)
            if last_order_dt and (now - last_order_dt).days >= 14:
                at_risk.append(c)

    if at_risk:
        insights.append(Insight(
            id="cust-002",
            type=InsightType.warning,
            category=InsightCategory.customers,
            title=f"{len(at_risk)} at-risk customer(s)",
            description=(
                f"{len(at_risk)} customer(s) have not ordered in 14+ days. "
                "A win-back campaign could re-engage them."
            ),
            impact=ImpactLevel.medium,
            action=ActionPayload(
                type="create_discount",
                label="Create 15% win-back discount",
                params={"code": "COMEBACK15", "percentage": 15},
            ),
        ))

    # New customer surge/decline — compare unique new customers in 7d orders
    active_emails_7d: set[str] = set()
    for o in orders_7d:
        if o.customer_email:
            active_emails_7d.add(o.customer_email)

    # New customers created in last 7 days
    cutoff_7d = now - timedelta(days=7)
    new_customers = [
        c for c in customers
        if c.created_at and _parse_date(c.created_at) and _parse_date(c.created_at) >= cutoff_7d  # type: ignore[operator]
    ]

    if len(new_customers) >= 5:
        insights.append(Insight(
            id="cust-003",
            type=InsightType.success,
            category=InsightCategory.customers,
            title=f"{len(new_customers)} new customers this week",
            description="Healthy new customer acquisition. Consider a welcome discount series.",
            impact=ImpactLevel.low,
            action=ActionPayload(
                type="create_discount",
                label="Create 10% new customer welcome code",
                params={"code": "WELCOME10", "percentage": 10},
            ),
        ))

    return insights


def _analyze_operations(
    orders_7d: list[Order],
    all_recent_orders: list[Order],
    now: datetime,
) -> list[Insight]:
    """Detect unfulfilled orders and high refund rates."""
    insights: list[Insight] = []

    # Unfulfilled orders older than 2 days
    cutoff_2d = now - timedelta(days=2)
    unfulfilled: list[Order] = []
    for o in all_recent_orders:
        created = _parse_date(o.created_at)
        if (
            created
            and created < cutoff_2d
            and o.fulfillment_status in (None, "unfulfilled", "partial")
            and o.financial_status == "paid"
        ):
            unfulfilled.append(o)

    if unfulfilled:
        insights.append(Insight(
            id="ops-001",
            type=InsightType.critical if len(unfulfilled) > 5 else InsightType.warning,
            category=InsightCategory.operations,
            title=f"{len(unfulfilled)} unfulfilled order(s) older than 2 days",
            description=(
                "Paid orders awaiting fulfillment degrade customer trust. "
                f"Oldest: order #{unfulfilled[0].order_number}."
            ),
            impact=ImpactLevel.high,
            action=ActionPayload(
                type="flag",
                label="Review unfulfilled orders immediately",
                params={"order_ids": [o.id for o in unfulfilled[:10]]},
            ),
        ))

    # High refund rate
    refunded = [o for o in all_recent_orders if o.financial_status in ("refunded", "partially_refunded")]
    if all_recent_orders:
        refund_rate = len(refunded) / len(all_recent_orders)
        if refund_rate > 0.05:
            insights.append(Insight(
                id="ops-002",
                type=InsightType.warning,
                category=InsightCategory.operations,
                title=f"Refund rate at {refund_rate * 100:.1f}%",
                description=(
                    f"{len(refunded)} of {len(all_recent_orders)} recent orders "
                    "were refunded. Investigate product quality or listing accuracy."
                ),
                impact=ImpactLevel.high if refund_rate > 0.10 else ImpactLevel.medium,
                action=ActionPayload(
                    type="flag",
                    label="Investigate high refund rate",
                    params={"refund_count": len(refunded), "total_orders": len(all_recent_orders)},
                ),
            ))

    return insights


def _compute_health_score(
    insights: list[Insight],
    summary: Summary,
) -> int:
    """Compute a 0-100 store health score based on insights and summary."""
    score = 80  # baseline

    for insight in insights:
        if insight.type == InsightType.critical:
            score -= 15
        elif insight.type == InsightType.warning:
            score -= 7
        elif insight.type == InsightType.success:
            score += 3

    # Revenue trend bonus/penalty
    if summary.revenue_trend == RevenueTrend.up:
        score += 5
    elif summary.revenue_trend == RevenueTrend.down:
        score -= 10

    # Low stock penalty
    if summary.low_stock_count > 5:
        score -= 5

    # Clamp to 0-100
    return max(0, min(100, score))


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/analyze", response_model=AnalyzeResponse)
async def analyze_store(db: AsyncSession = Depends(get_db)):
    """
    Aggregate all store data and run the rule-based analysis engine.

    Returns a health score, actionable insights, and a store summary.
    """
    now = _now()
    cutoff_7d = now - timedelta(days=7)
    cutoff_14d = now - timedelta(days=14)
    cutoff_7d_str = cutoff_7d.isoformat()
    cutoff_14d_str = cutoff_14d.isoformat()

    # ------------------------------------------------------------------
    # Fetch all data
    # ------------------------------------------------------------------
    products_result = await db.execute(select(Product))
    all_products: list[Product] = list(products_result.scalars().all())

    orders_result = await db.execute(select(Order))
    all_orders: list[Order] = list(orders_result.scalars().all())

    customers_result = await db.execute(select(Customer))
    all_customers: list[Customer] = list(customers_result.scalars().all())

    # ------------------------------------------------------------------
    # Partition orders by time window
    # ------------------------------------------------------------------
    orders_7d: list[Order] = []
    orders_prev_7d: list[Order] = []

    for o in all_orders:
        dt = _parse_date(o.processed_at)
        if not dt:
            continue
        if dt >= cutoff_7d:
            orders_7d.append(o)
        elif dt >= cutoff_14d:
            orders_prev_7d.append(o)

    # ------------------------------------------------------------------
    # Compute product velocity (units/day over last 7d)
    # ------------------------------------------------------------------
    product_units_7d: dict[str, int] = defaultdict(int)
    product_revenue_7d: dict[str, float] = defaultdict(float)

    for o in orders_7d:
        for item in (o.line_items or []):
            pid = item.get("product_id") or "unknown"
            qty = int(item.get("quantity", 0))
            amt = float(item.get("amount", 0))
            product_units_7d[pid] += qty
            product_revenue_7d[pid] += amt

    product_velocity: dict[str, float] = {
        pid: units / 7.0 for pid, units in product_units_7d.items()
    }

    product_orders_7d: dict[str, int] = defaultdict(int)
    for o in orders_7d:
        seen: set[str] = set()
        for item in (o.line_items or []):
            pid = item.get("product_id") or "unknown"
            if pid not in seen:
                product_orders_7d[pid] += 1
                seen.add(pid)

    # ------------------------------------------------------------------
    # Run analysis engines
    # ------------------------------------------------------------------
    insights: list[Insight] = []

    insights.extend(_analyze_inventory(all_products, product_velocity, product_orders_7d, now))

    rev_insights, rev_trend, rev_change_pct = _analyze_revenue(orders_7d, orders_prev_7d)
    insights.extend(rev_insights)

    insights.extend(_analyze_customers(all_customers, orders_7d, now))

    insights.extend(_analyze_operations(orders_7d, all_orders, now))

    # ------------------------------------------------------------------
    # Build summary
    # ------------------------------------------------------------------
    revenue_7d = sum(o.total_price for o in orders_7d)
    aov = revenue_7d / len(orders_7d) if orders_7d else 0.0
    low_stock = [p for p in all_products if p.status == "active" and 0 < p.inventory_total < 5]

    # Top product by revenue
    top_product_title = ""
    if product_revenue_7d:
        top_pid = max(product_revenue_7d, key=product_revenue_7d.get)  # type: ignore[arg-type]
        for p in all_products:
            if p.id == top_pid:
                top_product_title = p.title
                break
        if not top_product_title:
            top_product_title = top_pid

    # Active customers in 7d
    active_emails: set[str] = set()
    for o in orders_7d:
        if o.customer_email:
            active_emails.add(o.customer_email)

    summary = Summary(
        total_products=len([p for p in all_products if p.status == "active"]),
        total_orders_7d=len(orders_7d),
        revenue_7d=round(revenue_7d, 2),
        revenue_trend=rev_trend,
        revenue_change_pct=rev_change_pct,
        low_stock_count=len(low_stock),
        top_product=top_product_title,
        active_customers_7d=len(active_emails),
        avg_order_value=round(aov, 2),
    )

    # ------------------------------------------------------------------
    # Compute health score
    # ------------------------------------------------------------------
    score = _compute_health_score(insights, summary)

    # Sort insights: critical first, then warning, opportunity, success
    priority = {InsightType.critical: 0, InsightType.warning: 1, InsightType.opportunity: 2, InsightType.success: 3}
    insights.sort(key=lambda i: (priority.get(i.type, 99), i.impact != ImpactLevel.high))

    return AnalyzeResponse(score=score, insights=insights, summary=summary)


@router.post("/execute", response_model=ExecuteActionResponse)
async def execute_action(body: ExecuteActionRequest, request: Request):
    """
    Execute a recommended action from the analysis engine.

    Supported action types:
    - create_discount: Creates a Shopify discount code.
    - send_email: Logs an email notification (placeholder).
    - flag: Logs the flag for review.
    """
    action_type = body.type
    params = body.params

    if action_type == "create_discount":
        code = params.get("code", "AUTOPILOT")
        percentage = params.get("percentage", 10)

        shopify = request.app.state.shopify

        price_rule_data = {
            "price_rule": {
                "title": code,
                "target_type": "line_item",
                "target_selection": "all",
                "allocation_method": "across",
                "value_type": "percentage",
                "value": f"-{percentage}",
                "customer_selection": "all",
                "starts_at": "2024-01-01T00:00:00Z",
            }
        }
        try:
            price_rule_result = await shopify.rest("POST", "price_rules.json", json=price_rule_data)
            price_rule_id = price_rule_result.get("price_rule", {}).get("id")

            if not price_rule_id:
                return ExecuteActionResponse(
                    status="error",
                    message="Failed to create price rule on Shopify",
                    result={"details": price_rule_result},
                )

            discount_data = {"discount_code": {"code": code}}
            result = await shopify.rest(
                "POST",
                f"price_rules/{price_rule_id}/discount_codes.json",
                json=discount_data,
            )
            return ExecuteActionResponse(
                status="success",
                message=f"Discount code {code} created for {percentage}% off",
                result={"discount": result, "price_rule_id": price_rule_id},
            )
        except Exception as exc:
            logger.error("Failed to create discount: %s", exc)
            return ExecuteActionResponse(
                status="error",
                message=f"Shopify API error: {exc}",
                result={},
            )

    elif action_type == "send_email":
        to = params.get("to", "unknown")
        subject = params.get("subject", "Autopilot Notification")
        logger.info("AUTOPILOT EMAIL — To: %s | Subject: %s", to, subject)
        return ExecuteActionResponse(
            status="logged",
            message=f"Email to {to} logged (no email provider configured)",
            result={"to": to, "subject": subject},
        )

    elif action_type == "flag":
        logger.info("AUTOPILOT FLAG — Params: %s", params)
        return ExecuteActionResponse(
            status="flagged",
            message="Item flagged for manual review",
            result={"params": params},
        )

    else:
        return ExecuteActionResponse(
            status="error",
            message=f"Unknown action type: {action_type}",
            result={},
        )
