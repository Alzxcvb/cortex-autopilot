#!/usr/bin/env bash
#
# Master seed script — seeds ALL stores with themes + data from CLI.
# No manual steps. Run it and walk away.
#
# Usage:
#   cd HackathonStarterRepo
#   bash scripts/seed-all.sh
#
# What it does (in order):
#   1. Captures access tokens for all 30 apps (OAuth flow)
#   2. Seeds products, collections, customers, discounts
#   3. Seeds UTM-attributed orders (75 per store)
#   4. Pushes Dawn theme to each store
#
# Prerequisites:
#   - shopify CLI installed + logged in
#   - Python 3.10+ with httpx, python-dotenv
#   - scripts/apps.json with app credentials
#   - Already ran deploy-all-apps.sh (redirect URLs set to localhost:3456)
#   - jq installed

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPTS_DIR="$REPO_ROOT/scripts"
BACKEND_DIR="$REPO_ROOT/backend"
TOKENS_FILE="$SCRIPTS_DIR/tokens.json"
APPS_FILE="$SCRIPTS_DIR/apps.json"
DAWN_DIR="/tmp/dawn-theme"

echo "============================================="
echo " HACKATHON STORE SEEDER — FULL PIPELINE"
echo "============================================="
echo ""

# -----------------------------------------------------------
# Step 0: Preflight checks
# -----------------------------------------------------------
echo "[0/4] Preflight checks..."

if ! command -v jq &>/dev/null; then
    echo "  ✗ jq not found. Install: sudo apt install jq"
    exit 1
fi
echo "  ✓ jq"

if ! command -v shopify &>/dev/null; then
    echo "  ✗ shopify CLI not found"
    exit 1
fi
echo "  ✓ shopify CLI"

if ! command -v python3 &>/dev/null; then
    echo "  ✗ python3 not found"
    exit 1
fi
echo "  ✓ python3"

# Check python deps
python3 -c "import httpx" 2>/dev/null || { echo "  ✗ httpx not installed. Run: pip install httpx"; exit 1; }
echo "  ✓ httpx"

python3 -c "import dotenv" 2>/dev/null || { echo "  ✗ python-dotenv not installed. Run: pip install python-dotenv"; exit 1; }
echo "  ✓ python-dotenv"

if [ ! -f "$APPS_FILE" ]; then
    echo "  ✗ apps.json not found at $APPS_FILE"
    exit 1
fi
echo "  ✓ apps.json ($( jq 'length' "$APPS_FILE" ) apps)"

echo ""

# -----------------------------------------------------------
# Step 1: Capture tokens (if not already done)
# -----------------------------------------------------------
if [ -f "$TOKENS_FILE" ]; then
    EXISTING=$(jq 'length' "$TOKENS_FILE")
    TOTAL=$(jq 'length' "$APPS_FILE")
    if [ "$EXISTING" -ge "$TOTAL" ]; then
        echo "[1/4] Tokens already captured ($EXISTING/$TOTAL) — skipping"
    else
        echo "[1/4] Partial tokens found ($EXISTING/$TOTAL) — resuming capture..."
        echo "  This opens browser tabs. Click 'Install' on each one."
        echo "  Press Enter to start..."
        read -r
        cd "$SCRIPTS_DIR"
        python3 capture-tokens.py
        cd "$REPO_ROOT"
    fi
else
    echo "[1/4] Capturing access tokens..."
    echo "  This opens browser tabs. Click 'Install' on each one."
    echo "  Press Enter to start..."
    read -r
    cd "$SCRIPTS_DIR"
    python3 capture-tokens.py
    cd "$REPO_ROOT"
fi

# Verify tokens exist
if [ ! -f "$TOKENS_FILE" ]; then
    echo "  ✗ tokens.json not created. Token capture failed."
    exit 1
fi
echo "  ✓ tokens.json ($(jq 'length' "$TOKENS_FILE") tokens)"
echo ""

# -----------------------------------------------------------
# Step 2: Seed products, collections, customers, discounts
# -----------------------------------------------------------
echo "[2/4] Seeding product catalog to all stores..."
cd "$REPO_ROOT"
python3 backend/seed.py --tokens "$TOKENS_FILE" --all
echo "  ✓ Products, collections, customers, discounts seeded"
echo ""

# -----------------------------------------------------------
# Step 3: Seed UTM-attributed orders
# -----------------------------------------------------------
echo "[3/4] Seeding UTM-attributed orders (75 per store)..."
python3 backend/seed_orders.py --tokens "$TOKENS_FILE" --all --count 75
echo "  ✓ Orders seeded"
echo ""

# -----------------------------------------------------------
# Step 4: Push Dawn theme to each store
# -----------------------------------------------------------
echo "[4/4] Pushing Dawn theme to all stores..."

# Clone Dawn once if not cached
if [ ! -d "$DAWN_DIR" ]; then
    echo "  Cloning Dawn theme..."
    git clone --depth 1 https://github.com/Shopify/dawn.git "$DAWN_DIR"
else
    echo "  Dawn theme already cached at $DAWN_DIR"
fi

THEME_TOTAL=$(jq 'length' "$TOKENS_FILE")
THEME_CURRENT=0
THEME_FAILED=0

for APP_NAME in $(jq -r 'keys[]' "$TOKENS_FILE" | sort); do
    THEME_CURRENT=$((THEME_CURRENT + 1))
    STORE=$(jq -r ".\"$APP_NAME\".store" "$TOKENS_FILE")
    TOKEN=$(jq -r ".\"$APP_NAME\".access_token" "$TOKENS_FILE")

    echo "  [$THEME_CURRENT/$THEME_TOTAL] $STORE..."

    if shopify theme push --path "$DAWN_DIR" \
        --store "$STORE" \
        --unpublished \
        --json 2>/dev/null | jq -r '.theme.id' > /tmp/theme_id_$APP_NAME 2>/dev/null; then

        THEME_ID=$(cat /tmp/theme_id_$APP_NAME)
        echo "    ✓ Dawn pushed (theme_id: $THEME_ID)"

        # Publish it as the live theme using the Admin API
        curl -s -X PUT "https://$STORE/admin/api/2025-01/themes/$THEME_ID.json" \
            -H "X-Shopify-Access-Token: $TOKEN" \
            -H "Content-Type: application/json" \
            -d "{\"theme\": {\"id\": $THEME_ID, \"role\": \"main\"}}" > /dev/null 2>&1 \
            && echo "    ✓ Published as live theme" \
            || echo "    ⚠ Push succeeded but publish failed — set manually"
    else
        echo "    ✗ Failed to push theme to $STORE"
        THEME_FAILED=$((THEME_FAILED + 1))
    fi

    rm -f /tmp/theme_id_$APP_NAME
    sleep 2  # Rate limit
done

echo ""
echo "============================================="
echo " ALL DONE"
echo "============================================="
echo ""
echo " Tokens:   $(jq 'length' "$TOKENS_FILE") captured"
echo " Products: 25 per store (with collections, customers, discounts)"
echo " Orders:   75 per store (UTM attributed)"
echo " Themes:   Dawn pushed to $((THEME_TOTAL - THEME_FAILED))/$THEME_TOTAL stores"
echo ""
echo " Next: distribute tokens to teams (credential cards)"
echo "============================================="
