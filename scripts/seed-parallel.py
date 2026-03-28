#!/usr/bin/env python3
"""
Parallel store seeder — seeds data + themes across all stores concurrently.

Runs 5 stores at a time. Each store gets:
  - Products, collections, customers, discounts (via seed.py)
  - 75 UTM-attributed orders (via seed_orders.py)
  - Dawn theme installed as live theme (via Shopify API)

Usage:
    cd HackathonStarterRepo
    python3 scripts/seed-parallel.py                          # All stores
    python3 scripts/seed-parallel.py --concurrency 10         # 10 at once
    python3 scripts/seed-parallel.py --skip-theme             # Data only
    python3 scripts/seed-parallel.py --only gzh-01 gzh-02     # Specific stores
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).parent.parent
TOKENS_FILE = REPO_ROOT / "scripts" / "tokens.json"
SEED_SCRIPT = REPO_ROOT / "backend" / "seed.py"
ORDER_SCRIPT = REPO_ROOT / "backend" / "seed_orders.py"
DAWN_ZIP = "https://github.com/Shopify/dawn/archive/refs/heads/main.zip"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("seed")


# ---------------------------------------------------------------------------
# Per-store pipeline
# ---------------------------------------------------------------------------

async def seed_one_store(
    name: str,
    store: str,
    token: str,
    order_count: int,
    skip_theme: bool,
    semaphore: asyncio.Semaphore,
):
    async with semaphore:
        t0 = time.time()
        log.info("[%s] Starting...", name)

        # Phase 1: Products + collections + customers + discounts
        log.info("[%s] Phase 1: Seeding catalog...", name)
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(SEED_SCRIPT),
            "--store", store, "--token", token,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            log.error("[%s] Phase 1 FAILED:\n%s", name, stderr.decode()[-500:])
        else:
            log.info("[%s] Phase 1 done (catalog seeded)", name)

        # Phase 2: UTM-attributed orders
        log.info("[%s] Phase 2: Seeding %d orders...", name, order_count)
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(ORDER_SCRIPT),
            "--store", store, "--token", token, "--count", str(order_count),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            log.error("[%s] Phase 2 FAILED:\n%s", name, stderr.decode()[-500:])
        else:
            log.info("[%s] Phase 2 done (orders seeded)", name)

        # Phase 3: Dawn theme
        if not skip_theme:
            log.info("[%s] Phase 3: Installing Dawn theme...", name)
            try:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    # Check if Dawn already exists
                    resp = await client.get(
                        f"https://{store}/admin/api/2025-01/themes.json",
                        headers={"X-Shopify-Access-Token": token},
                    )
                    resp.raise_for_status()
                    themes = resp.json().get("themes", [])
                    dawn_exists = any(
                        t["name"].lower().startswith("dawn") and t["role"] == "main"
                        for t in themes
                    )

                    if dawn_exists:
                        log.info("[%s] Dawn already live — skipping", name)
                    else:
                        # Create Dawn theme from GitHub zip
                        resp = await client.post(
                            f"https://{store}/admin/api/2025-01/themes.json",
                            headers={
                                "X-Shopify-Access-Token": token,
                                "Content-Type": "application/json",
                            },
                            json={
                                "theme": {
                                    "name": "Dawn",
                                    "src": DAWN_ZIP,
                                    "role": "main",
                                }
                            },
                        )
                        if resp.status_code == 429:
                            retry = float(resp.headers.get("Retry-After", "5"))
                            log.warning("[%s] Rate limited on theme, waiting %.0fs", name, retry)
                            await asyncio.sleep(retry)
                            resp = await client.post(
                                f"https://{store}/admin/api/2025-01/themes.json",
                                headers={
                                    "X-Shopify-Access-Token": token,
                                    "Content-Type": "application/json",
                                },
                                json={
                                    "theme": {
                                        "name": "Dawn",
                                        "src": DAWN_ZIP,
                                        "role": "main",
                                    }
                                },
                            )
                        resp.raise_for_status()
                        theme_id = resp.json()["theme"]["id"]
                        log.info("[%s] Phase 3 done (Dawn theme id=%d)", name, theme_id)

            except Exception as e:
                log.error("[%s] Phase 3 FAILED: %s", name, e)

        elapsed = time.time() - t0
        log.info("[%s] COMPLETE in %.0fs", name, elapsed)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    parser = argparse.ArgumentParser(description="Parallel store seeder")
    parser.add_argument("--concurrency", type=int, default=5, help="Stores to seed in parallel (default: 5)")
    parser.add_argument("--orders", type=int, default=75, help="Orders per store (default: 75)")
    parser.add_argument("--skip-theme", action="store_true", help="Skip theme installation")
    parser.add_argument("--only", nargs="+", help="Only seed these stores (e.g. gzh-01 gzh-02)")
    args = parser.parse_args()

    if not TOKENS_FILE.exists():
        print(f"ERROR: {TOKENS_FILE} not found. Run capture-tokens-auto.py first.")
        sys.exit(1)

    with open(TOKENS_FILE) as f:
        all_tokens = json.load(f)

    # Filter if --only specified
    if args.only:
        tokens = {k: v for k, v in all_tokens.items() if k in args.only}
        missing = set(args.only) - set(tokens.keys())
        if missing:
            print(f"WARNING: No tokens for: {', '.join(sorted(missing))}")
    else:
        tokens = all_tokens

    if not tokens:
        print("No stores to seed!")
        sys.exit(1)

    print(f"\n{'='*55}")
    print(f"  Parallel Store Seeder")
    print(f"  Stores: {len(tokens)}  |  Concurrency: {args.concurrency}  |  Orders: {args.orders}/store")
    print(f"  Theme: {'skip' if args.skip_theme else 'Dawn (from GitHub)'}")
    print(f"{'='*55}\n")

    semaphore = asyncio.Semaphore(args.concurrency)
    t0 = time.time()

    tasks = [
        seed_one_store(
            name=name,
            store=info["store"],
            token=info["access_token"],
            order_count=args.orders,
            skip_theme=args.skip_theme,
            semaphore=semaphore,
        )
        for name, info in sorted(tokens.items())
    ]

    await asyncio.gather(*tasks)

    elapsed = time.time() - t0
    print(f"\n{'='*55}")
    print(f"  ALL DONE — {len(tokens)} stores seeded in {elapsed:.0f}s")
    print(f"{'='*55}\n")


if __name__ == "__main__":
    asyncio.run(main())
