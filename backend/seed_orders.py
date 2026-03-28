#!/usr/bin/env python3
"""
Seed orders with UTM attribution data into Shopify dev stores.

Creates realistic orders with landing_site URLs containing UTM params
so teams can build attribution dashboards, Sankey diagrams, etc.

Usage:
  python backend/seed_orders.py                              # Uses .env
  python backend/seed_orders.py --tokens tokens.json --all   # All stores
  python backend/seed_orders.py --store gzh-07.myshopify.com --token shpat_xxx
"""
import argparse
import asyncio
import json
import logging
import os
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import httpx
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# UTM Attribution Data — realistic channel/campaign/content combos
# ---------------------------------------------------------------------------

CHANNELS = [
    {
        "source": "tiktok",
        "medium": "ugc",
        "referring_site": "https://www.tiktok.com",
        "campaigns": [
            {"campaign": "summer_drop", "contents": [
                "creator_emma_unbox", "creator_liam_review", "creator_sofia_haul",
                "creator_ava_ootd", "creator_noah_try_on",
            ]},
            {"campaign": "best_sellers_push", "contents": [
                "creator_mia_top5", "creator_oliver_fav", "creator_luna_picks",
            ]},
            {"campaign": "flash_sale_24h", "contents": [
                "creator_emma_urgency", "creator_james_deal",
            ]},
        ],
        "weight": 30,  # 30% of traffic
    },
    {
        "source": "instagram",
        "medium": "paid",
        "referring_site": "https://www.instagram.com",
        "campaigns": [
            {"campaign": "reel_conversions", "contents": [
                "reel_product_demo", "reel_lifestyle_shoot", "reel_before_after",
                "reel_creator_collab",
            ]},
            {"campaign": "story_swipeup", "contents": [
                "story_new_arrivals", "story_flash_deal", "story_behind_scenes",
            ]},
            {"campaign": "ig_shop_retarget", "contents": [
                "retarget_cart_abandon", "retarget_viewed_product",
            ]},
        ],
        "weight": 25,
    },
    {
        "source": "facebook",
        "medium": "cpc",
        "referring_site": "https://www.facebook.com",
        "campaigns": [
            {"campaign": "lookalike_buyers", "contents": [
                "ad_carousel_bestsellers", "ad_single_hero", "ad_video_testimonial",
            ]},
            {"campaign": "retargeting_30d", "contents": [
                "ad_dynamic_product", "ad_cart_reminder",
            ]},
        ],
        "weight": 15,
    },
    {
        "source": "youtube",
        "medium": "influencer",
        "referring_site": "https://www.youtube.com",
        "campaigns": [
            {"campaign": "creator_reviews", "contents": [
                "creator_jake_10min_review", "creator_sarah_comparison",
                "creator_mike_unboxing",
            ]},
            {"campaign": "shorts_push", "contents": [
                "short_quick_look", "short_outfit_inspo",
            ]},
        ],
        "weight": 10,
    },
    {
        "source": "email",
        "medium": "newsletter",
        "referring_site": None,
        "campaigns": [
            {"campaign": "weekly_digest", "contents": [
                "email_top_picks", "email_new_arrivals", "email_staff_picks",
            ]},
            {"campaign": "abandon_cart_flow", "contents": [
                "email_cart_reminder_1h", "email_cart_reminder_24h",
            ]},
            {"campaign": "win_back_30d", "contents": [
                "email_miss_you", "email_special_offer",
            ]},
        ],
        "weight": 10,
    },
    {
        "source": "google",
        "medium": "cpc",
        "referring_site": "https://www.google.com",
        "campaigns": [
            {"campaign": "brand_search", "contents": [
                "ad_brand_exact", "ad_brand_broad",
            ]},
            {"campaign": "shopping_feed", "contents": [
                "pla_tshirts", "pla_hoodies", "pla_accessories",
            ]},
        ],
        "weight": 7,
    },
    {
        "source": "direct",
        "medium": "none",
        "referring_site": None,
        "campaigns": [
            {"campaign": "(direct)", "contents": ["(direct)"]},
        ],
        "weight": 3,
    },
]

# Build weighted channel picker
_CHANNEL_POOL = []
for ch in CHANNELS:
    _CHANNEL_POOL.extend([ch] * ch["weight"])

FIRST_NAMES = [
    "Emma", "Liam", "Olivia", "Noah", "Ava", "Elijah", "Sophia", "James",
    "Isabella", "Oliver", "Mia", "Lucas", "Harper", "Mason", "Evelyn",
    "Logan", "Aria", "Alexander", "Luna", "Ethan", "Charlotte", "Henry",
    "Amelia", "Sebastian", "Scarlett", "Jack", "Grace", "Owen", "Chloe",
    "Daniel",
]
LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
    "Davis", "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez",
    "Wilson", "Anderson", "Thomas", "Taylor", "Moore", "Jackson", "Martin",
    "Lee", "Perez", "Thompson", "White", "Harris", "Sanchez", "Clark",
    "Ramirez", "Lewis", "Robinson",
]
DISCOUNT_CODES = ["HACK10", "WELCOME15", "SAVE20", "DEMO25", "FIRST10"]


def _random_email(first: str, last: str) -> str:
    domains = ["example.com", "test.io", "hackathon.dev", "demo.org", "mail.test"]
    return f"{first.lower()}.{last.lower()}{random.randint(1, 999)}@{random.choice(domains)}"


def _pick_attribution(product_handle: str) -> dict:
    """Pick a random channel/campaign/content combo and build UTM landing URL."""
    channel = random.choice(_CHANNEL_POOL)
    campaign = random.choice(channel["campaigns"])
    content = random.choice(campaign["contents"])

    source = channel["source"]
    medium = channel["medium"]
    camp = campaign["campaign"]

    if source == "direct":
        return {
            "landing_site": f"/products/{product_handle}",
            "referring_site": None,
        }

    landing = (
        f"/products/{product_handle}"
        f"?utm_source={source}"
        f"&utm_medium={medium}"
        f"&utm_campaign={camp}"
        f"&utm_content={content}"
    )
    return {
        "landing_site": landing,
        "referring_site": channel["referring_site"],
    }


def _random_past_date(days_back: int = 30) -> str:
    """Generate a random datetime within the past N days."""
    delta = timedelta(
        days=random.randint(0, days_back),
        hours=random.randint(0, 23),
        minutes=random.randint(0, 59),
    )
    dt = datetime.now(timezone.utc) - delta
    return dt.strftime("%Y-%m-%dT%H:%M:%S%z")


class OrderSeeder:
    """Seeds a Shopify dev store with attributed orders."""

    def __init__(self, store_url: str, access_token: str, api_version: str = "2025-01"):
        self.store_url = store_url
        self.base_url = f"https://{store_url}/admin/api/{api_version}"
        self.headers = {
            "X-Shopify-Access-Token": access_token,
            "Content-Type": "application/json",
        }
        self.client = httpx.AsyncClient(timeout=30.0)

    async def rest(self, method: str, path: str, json_data: dict = None) -> dict:
        url = f"{self.base_url}/{path.lstrip('/')}"
        resp = await self.client.request(method, url, json=json_data, headers=self.headers)
        if resp.status_code == 429:
            retry_after = float(resp.headers.get("Retry-After", "2"))
            logger.warning("Rate limited on %s, waiting %.1fs...", self.store_url, retry_after)
            await asyncio.sleep(retry_after)
            resp = await self.client.request(method, url, json=json_data, headers=self.headers)
        resp.raise_for_status()
        return resp.json()

    async def get_products(self) -> list:
        """Fetch all active products from the store."""
        products = []
        url = "products.json?status=active&limit=250"
        result = await self.rest("GET", url)
        products.extend(result.get("products", []))
        return products

    async def seed_orders(self, count: int = 75) -> int:
        """Create orders with UTM attribution data spread across the past 30 days."""
        products = await self.get_products()
        if not products:
            logger.warning("No products found on %s — run seed.py first!", self.store_url)
            return 0

        created = 0
        for i in range(count):
            try:
                # Pick 1-4 random products
                order_products = random.sample(products, min(random.randint(1, 4), len(products)))

                # Build line items from actual variant IDs
                line_items = []
                primary_handle = order_products[0].get("handle", "product")
                for prod in order_products:
                    variants = prod.get("variants", [])
                    if not variants:
                        continue
                    variant = random.choice(variants)
                    line_items.append({
                        "variant_id": variant["id"],
                        "quantity": random.randint(1, 3),
                    })

                if not line_items:
                    continue

                # Random customer
                first = random.choice(FIRST_NAMES)
                last = random.choice(LAST_NAMES)

                # UTM attribution
                attribution = _pick_attribution(primary_handle)

                # Build order
                order_data = {
                    "order": {
                        "line_items": line_items,
                        "financial_status": "paid",
                        "customer": {
                            "first_name": first,
                            "last_name": last,
                            "email": _random_email(first, last),
                        },
                        "landing_site": attribution["landing_site"],
                        "referring_site": attribution["referring_site"],
                        "tags": "seeded,hackathon,attributed",
                        "note": f"Seeded order with UTM attribution",
                        "processed_at": _random_past_date(30),
                    }
                }

                # ~20% chance: add a discount code
                if random.random() < 0.20:
                    code = random.choice(DISCOUNT_CODES)
                    order_data["order"]["discount_codes"] = [
                        {"code": code, "amount": "10.00", "type": "percentage"}
                    ]

                # ~10% chance: partially fulfilled
                if random.random() < 0.10:
                    order_data["order"]["fulfillment_status"] = "partial"
                elif random.random() < 0.60:
                    order_data["order"]["fulfillment_status"] = "fulfilled"

                # ~5% chance: refunded
                if random.random() < 0.05:
                    order_data["order"]["financial_status"] = "refunded"
                elif random.random() < 0.03:
                    order_data["order"]["financial_status"] = "partially_refunded"

                result = await self.rest("POST", "orders.json", order_data)
                order = result.get("order", {})
                created += 1

                if created % 10 == 0:
                    logger.info(
                        "[%s] %d/%d orders created...",
                        self.store_url, created, count,
                    )

                # Rate limit safety — Shopify allows ~2 req/sec for REST
                await asyncio.sleep(0.55)

            except Exception as e:
                logger.error("[%s] Failed to create order %d: %s", self.store_url, i + 1, e)
                await asyncio.sleep(1)

        logger.info("[%s] Done — %d orders seeded with UTM attribution", self.store_url, created)
        return created

    async def close(self):
        await self.client.aclose()


async def seed_store(store_url: str, access_token: str, order_count: int = 75):
    """Seed a single store with attributed orders."""
    logger.info("Seeding orders on %s (%d orders)...", store_url, order_count)
    seeder = OrderSeeder(store_url, access_token)
    try:
        created = await seeder.seed_orders(order_count)
        logger.info("Seeding complete for %s — %d orders created", store_url, created)
    finally:
        await seeder.close()


async def seed_all(tokens_path: str, order_count: int = 75):
    """Seed all stores from a tokens.json file."""
    with open(tokens_path) as f:
        tokens = json.load(f)

    total = 0
    for name, info in sorted(tokens.items()):
        logger.info("--- Seeding orders: %s (%s) ---", name, info["store"])
        seeder = OrderSeeder(info["store"], info["access_token"])
        try:
            created = await seeder.seed_orders(order_count)
            total += created
        finally:
            await seeder.close()
        logger.info("--- Done: %s (%d orders) ---\n", name, created)

    logger.info("=== ALL DONE: %d total orders across %d stores ===", total, len(tokens))


def main():
    parser = argparse.ArgumentParser(description="Seed Shopify stores with attributed orders")
    parser.add_argument("--store", help="Store URL (e.g. gzh-01.myshopify.com)")
    parser.add_argument("--token", help="Access token")
    parser.add_argument("--tokens", help="Path to tokens.json")
    parser.add_argument("--all", action="store_true", help="Seed all stores from tokens.json")
    parser.add_argument("--count", type=int, default=75, help="Orders per store (default: 75)")
    args = parser.parse_args()

    if args.tokens and args.all:
        asyncio.run(seed_all(args.tokens, args.count))
    elif args.store and args.token:
        asyncio.run(seed_store(args.store, args.token, args.count))
    else:
        env_path = Path(__file__).parent.parent / ".env"
        if env_path.exists():
            load_dotenv(env_path)
        else:
            load_dotenv()
        store_url = os.getenv("SHOPIFY_STORE_URL")
        access_token = os.getenv("SHOPIFY_ACCESS_TOKEN")
        if not store_url or not access_token:
            print("Error: Set SHOPIFY_STORE_URL and SHOPIFY_ACCESS_TOKEN in .env, or use --store/--token flags")
            sys.exit(1)
        asyncio.run(seed_store(store_url, access_token, args.count))


if __name__ == "__main__":
    main()
