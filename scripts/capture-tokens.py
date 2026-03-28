#!/usr/bin/env python3
"""
Capture Shopify access tokens for all hackathon apps.

This starts a local server, opens the Shopify OAuth URL for each app,
and captures the access token when Shopify redirects back.

Usage:
    python scripts/capture-tokens.py

Prerequisites:
    - pip install flask httpx
    - All apps must have redirect_url set to http://localhost:3456/auth/callback
    - Run deploy-scopes.sh first with updated redirect URLs

Output:
    tokens.json — mapping of store name → access token
"""

import json
import os
import sys
import time
import webbrowser
import hashlib
import hmac
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import threading

# Load app credentials from a local JSON file (not checked into git)
# Create apps.json with format: {"gzh-01": {"client_id": "...", "secret": "shpss_...", "store": "gzh-01.myshopify.com"}, ...}
APPS_FILE = os.path.join(os.path.dirname(__file__), "apps.json")
if os.path.exists(APPS_FILE):
    with open(APPS_FILE) as f:
        APPS = json.load(f)
else:
    print("ERROR: apps.json not found. Create it with your app credentials.")
    print("Format: {\"gzh-01\": {\"client_id\": \"...\", \"secret\": \"shpss_...\", \"store\": \"gzh-01.myshopify.com\"}, ...}")
    sys.exit(1)

ALL_SCOPES = "read_products,write_products,read_orders,write_orders,read_customers,write_customers,read_inventory,write_inventory,read_fulfillments,write_fulfillments,read_shipping,write_shipping,read_analytics,read_themes,write_themes,read_script_tags,write_script_tags,read_content,write_content,read_price_rules,write_price_rules,read_discounts,write_discounts,read_marketing_events,write_marketing_events,read_reports,read_draft_orders,write_draft_orders,read_locations,read_files,write_files,read_locales,write_locales,read_metaobjects,write_metaobjects,read_metaobject_definitions,write_metaobject_definitions,read_online_store_pages,read_online_store_navigation,write_online_store_navigation,read_gift_cards,write_gift_cards"

REDIRECT_URI = "http://localhost:3456/auth/callback"
PORT = 3456

tokens = {}
current_app = None

class OAuthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global current_app, tokens

        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if parsed.path == "/auth/callback":
            code = params.get("code", [None])[0]
            shop = params.get("shop", [None])[0]

            if code and current_app:
                # Exchange code for permanent token
                app_info = APPS[current_app]
                import urllib.request

                token_url = f"https://{shop}/admin/oauth/access_token"
                data = json.dumps({
                    "client_id": app_info["client_id"],
                    "client_secret": app_info["secret"],
                    "code": code
                }).encode()

                req = urllib.request.Request(token_url, data=data, headers={"Content-Type": "application/json"})
                try:
                    resp = urllib.request.urlopen(req)
                    result = json.loads(resp.read())
                except Exception as e:
                    error_body = e.read().decode() if hasattr(e, 'read') else str(e)
                    print(f"  ✗ Token exchange failed: {error_body}")
                    print(f"    URL: {token_url}")
                    print(f"    client_id: {app_info['client_id'][:8]}...")
                    print(f"    shop: {shop}")
                    self.send_response(500)
                    self.end_headers()
                    self.wfile.write(f"Token exchange failed: {error_body}".encode())
                    return

                access_token = result.get("access_token")
                if access_token:
                    tokens[current_app] = {
                        "store": shop,
                        "access_token": access_token,
                        "client_id": app_info["client_id"],
                        "api_secret": app_info["secret"]
                    }

                    # Save after each capture
                    with open("tokens.json", "w") as f:
                        json.dump(tokens, f, indent=2)

                    print(f"  ✓ {current_app} — token captured ({access_token[:12]}...)")

                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(f"<h1>✓ {current_app} token captured!</h1><p>Close this tab and check the terminal.</p>".encode())
                    return

            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Error capturing token")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # Suppress default logging

def main():
    global current_app

    # Load existing tokens if any
    if os.path.exists("tokens.json"):
        with open("tokens.json") as f:
            tokens.update(json.load(f))

    server = HTTPServer(("localhost", PORT), OAuthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    print(f"\nOAuth capture server running on http://localhost:{PORT}")
    print(f"Tokens will be saved to tokens.json\n")

    remaining = {k: v for k, v in APPS.items() if k not in tokens}

    if not remaining:
        print("All tokens already captured!")
        return

    print(f"{len(remaining)} apps remaining ({len(tokens)} already captured)\n")

    for app_name in sorted(remaining.keys()):
        current_app = app_name
        app_info = APPS[app_name]
        store = app_info["store"]
        client_id = app_info["client_id"]

        install_url = (
            f"https://{store}/admin/oauth/authorize"
            f"?client_id={client_id}"
            f"&scope={ALL_SCOPES}"
            f"&redirect_uri={REDIRECT_URI}"
        )

        print(f"\n[{app_name}] Install URL for {store}:")
        print(f"  {install_url}")
        print(f"  → Open this URL in your browser and click 'Install'")
        try:
            webbrowser.open(install_url)
        except Exception:
            pass  # Browser open failed — user can copy-paste the URL

        # Wait for callback
        while app_name not in tokens:
            time.sleep(0.5)

        time.sleep(1)  # Brief pause between apps

    print(f"\n{'='*50}")
    print(f"Done! {len(tokens)} tokens captured → tokens.json")
    print(f"{'='*50}\n")

    server.shutdown()

if __name__ == "__main__":
    main()
