#!/usr/bin/env python3
"""
Automated token capture using Playwright.
Zero manual clicking — browser opens, script handles OAuth flow for all 30 apps.

Usage:
    cd HackathonStarterRepo
    python3 scripts/capture-tokens-auto.py

Prerequisites:
    - pip install playwright && python -m playwright install chromium
    - scripts/apps.json with app credentials
    - All apps deployed with redirect_url = http://localhost:3456/auth/callback
      (run deploy-all-apps.sh first)

The script:
    1. Starts a local callback server on :3456
    2. Opens a headed browser (you'll see it)
    3. For each app: visits OAuth URL → clicks Install → captures token
    4. If login is needed, pauses and lets you log in (first time only)
    5. Writes tokens.json
"""

import asyncio
import json
import os
import sys
import threading
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from playwright.async_api import async_playwright

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent
APPS_FILE = SCRIPT_DIR / "apps.json"
TOKENS_FILE = SCRIPT_DIR / "tokens.json"
PORT = 3456
REDIRECT_URI = f"http://localhost:{PORT}/auth/callback"

SCOPES = (
    "read_products,write_products,read_orders,write_orders,"
    "read_customers,write_customers,read_inventory,write_inventory,"
    "read_fulfillments,write_fulfillments,read_analytics,"
    "read_themes,write_themes,read_script_tags,write_script_tags,"
    "read_content,write_content,read_price_rules,write_price_rules,"
    "read_discounts,write_discounts,read_draft_orders,write_draft_orders,"
    "read_locations,read_files,write_files"
)

# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------

tokens: dict = {}
token_events: dict[str, asyncio.Event] = {}
loop = None  # Will be set to the main event loop


# ---------------------------------------------------------------------------
# OAuth callback server — runs in a background thread
# ---------------------------------------------------------------------------

class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path != "/auth/callback":
            self.send_response(404)
            self.end_headers()
            return

        params = parse_qs(parsed.query)
        code = params.get("code", [None])[0]
        shop = params.get("shop", [None])[0]

        if not code or not shop:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Missing code or shop")
            return

        # Find the app by store domain
        app_name = None
        app_info = None
        with open(APPS_FILE) as f:
            apps = json.load(f)
        for name, info in apps.items():
            if info["store"] == shop:
                app_name = name
                app_info = info
                break

        if not app_name:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(f"Unknown store: {shop}".encode())
            return

        # Exchange code for token
        token_url = f"https://{shop}/admin/oauth/access_token"
        data = json.dumps({
            "client_id": app_info["client_id"],
            "client_secret": app_info["secret"],
            "code": code,
        }).encode()
        req = urllib.request.Request(
            token_url, data=data, headers={"Content-Type": "application/json"}
        )

        try:
            resp = urllib.request.urlopen(req)
            result = json.loads(resp.read())
            access_token = result.get("access_token")
        except Exception as e:
            err = e.read().decode() if hasattr(e, "read") else str(e)
            print(f"  \u2717 Token exchange failed for {app_name}: {err}")
            self.send_response(500)
            self.end_headers()
            self.wfile.write(f"Token exchange failed: {err}".encode())
            return

        if access_token:
            tokens[app_name] = {
                "store": shop,
                "access_token": access_token,
                "client_id": app_info["client_id"],
                "api_secret": app_info["secret"],
            }
            # Persist after each capture
            with open(TOKENS_FILE, "w") as f:
                json.dump(tokens, f, indent=2)

            print(f"  \u2713 {app_name} — token captured ({access_token[:12]}...)")

            # Signal the async event
            if app_name in token_events and loop:
                loop.call_soon_threadsafe(token_events[app_name].set)

        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(
            f"<h1>\u2713 {app_name}</h1><p>Token captured. This tab will close.</p>"
            f"<script>window.close()</script>".encode()
        )

    def log_message(self, *args):
        pass  # Suppress default logging


def start_callback_server():
    server = HTTPServer(("localhost", PORT), CallbackHandler)
    server.serve_forever()


# ---------------------------------------------------------------------------
# Main automation
# ---------------------------------------------------------------------------

async def main():
    global loop
    loop = asyncio.get_event_loop()

    # Load apps
    with open(APPS_FILE) as f:
        apps = json.load(f)

    # Load existing tokens
    if TOKENS_FILE.exists():
        with open(TOKENS_FILE) as f:
            tokens.update(json.load(f))

    remaining = {k: v for k, v in apps.items() if k not in tokens}
    if not remaining:
        print(f"All {len(apps)} tokens already captured!")
        return

    print(f"\n{'='*50}")
    print(f" Automated Token Capture")
    print(f" {len(tokens)} already done, {len(remaining)} remaining")
    print(f"{'='*50}\n")

    # Start callback server
    server_thread = threading.Thread(target=start_callback_server, daemon=True)
    server_thread.start()
    print(f"Callback server running on http://localhost:{PORT}\n")

    # Create async events for each remaining app
    for name in remaining:
        token_events[name] = asyncio.Event()

    # Launch browser
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        first = True
        for i, (app_name, app_info) in enumerate(sorted(remaining.items()), 1):
            store = app_info["store"]
            client_id = app_info["client_id"]
            oauth_url = (
                f"https://{store}/admin/oauth/authorize"
                f"?client_id={client_id}"
                f"&scope={SCOPES}"
                f"&redirect_uri={REDIRECT_URI}"
            )

            print(f"[{i}/{len(remaining)}] {app_name} ({store})...")

            await page.goto(oauth_url, wait_until="domcontentloaded")
            await asyncio.sleep(2)

            # Check if we landed on the callback (already installed)
            if "localhost" in page.url and "/auth/callback" in page.url:
                print(f"  \u2713 Already installed, token captured via redirect")
                await asyncio.sleep(1)
                continue

            # Check if we need to log in
            current_url = page.url
            if "/login" in current_url or "accounts.shopify.com" in current_url:
                if first:
                    print(f"  \u26a0 Login required. Please log in in the browser window.")
                    print(f"    Waiting for you to complete login...")
                    first = False

                # Wait until we're past the login page (up to 120s for first login)
                for _ in range(240):
                    await asyncio.sleep(0.5)
                    current_url = page.url
                    if "/login" not in current_url and "accounts.shopify.com" not in current_url:
                        break
                else:
                    print(f"  \u2717 Timeout waiting for login on {app_name}")
                    continue

                await asyncio.sleep(2)

            # Now we should be on the OAuth approval page
            # Look for the Install button
            try:
                # Try multiple selectors — Shopify changes these
                install_btn = None
                for selector in [
                    'button:has-text("Install app")',
                    'button:has-text("Install")',
                    '[type="submit"]:has-text("Install")',
                    'button.Polaris-Button--primary',
                ]:
                    try:
                        install_btn = await page.wait_for_selector(selector, timeout=5000)
                        if install_btn:
                            break
                    except Exception:
                        continue

                if install_btn:
                    await install_btn.click()
                    print(f"  Clicked Install, waiting for token...")
                else:
                    print(f"  \u26a0 No Install button found. Page: {page.url[:80]}")
                    print(f"    Please click Install manually in the browser.")

            except Exception as e:
                print(f"  \u26a0 Could not auto-click Install: {e}")
                print(f"    Please click Install manually in the browser.")

            # Wait for token capture (up to 30s)
            try:
                await asyncio.wait_for(token_events[app_name].wait(), timeout=30)
            except asyncio.TimeoutError:
                # Check if token arrived anyway
                if app_name not in tokens:
                    print(f"  \u2717 Timeout waiting for token on {app_name}")
                    continue

            await asyncio.sleep(1)

        await browser.close()

    # Final report
    print(f"\n{'='*50}")
    print(f" Done! {len(tokens)}/{len(apps)} tokens captured")
    print(f" Saved to: {TOKENS_FILE}")
    print(f"{'='*50}\n")

    if len(tokens) < len(apps):
        missing = set(apps.keys()) - set(tokens.keys())
        print(f" Missing: {', '.join(sorted(missing))}")


if __name__ == "__main__":
    asyncio.run(main())
