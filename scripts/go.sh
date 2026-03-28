#!/usr/bin/env bash
#
# Master hackathon setup — one command does everything.
#
# Usage:
#   cd HackathonStarterRepo
#   bash scripts/go.sh
#
# Pipeline:
#   1. Deploy baseline app config to all 30 apps        (~3 min)
#   2. Capture access tokens via Playwright automation   (~8 min)
#   3. Parallel seed: products + orders + Dawn theme     (~15 min)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPTS="$REPO_ROOT/scripts"
TOKENS_FILE="$SCRIPTS/tokens.json"
APPS_FILE="$SCRIPTS/apps.json"

echo ""
echo "  ╔═══════════════════════════════════════════════╗"
echo "  ║  HACKATHON STORE SETUP — FULL PIPELINE        ║"
echo "  ║  30 stores · data + themes · ~25 min          ║"
echo "  ╚═══════════════════════════════════════════════╝"
echo ""

# -----------------------------------------------------------
# Preflight
# -----------------------------------------------------------
echo "[preflight] Checking dependencies..."

MISSING=0
for cmd in jq shopify python3; do
    if command -v "$cmd" &>/dev/null; then
        echo "  ✓ $cmd"
    else
        echo "  ✗ $cmd NOT FOUND"
        MISSING=1
    fi
done

python3 -c "import httpx" 2>/dev/null && echo "  ✓ httpx" || { echo "  ✗ httpx (pip install httpx)"; MISSING=1; }
python3 -c "import dotenv" 2>/dev/null && echo "  ✓ python-dotenv" || { echo "  ✗ python-dotenv (pip install python-dotenv)"; MISSING=1; }
python3 -c "from playwright.async_api import async_playwright" 2>/dev/null && echo "  ✓ playwright" || { echo "  ✗ playwright (pip install playwright && python3 -m playwright install chromium)"; MISSING=1; }

[ ! -f "$APPS_FILE" ] && { echo "  ✗ apps.json not found"; MISSING=1; } || echo "  ✓ apps.json ($(jq 'length' "$APPS_FILE") apps)"

if [ "$MISSING" -eq 1 ]; then
    echo ""
    echo "Fix missing dependencies above, then re-run."
    exit 1
fi

TOTAL_APPS=$(jq 'length' "$APPS_FILE")
echo ""

# -----------------------------------------------------------
# Step 1: Deploy baseline config
# -----------------------------------------------------------
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " STEP 1/3: Deploy app config to $TOTAL_APPS apps"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

bash "$SCRIPTS/deploy-all-apps.sh"

echo ""

# -----------------------------------------------------------
# Step 2: Capture tokens
# -----------------------------------------------------------
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " STEP 2/3: Capture access tokens"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

if [ -f "$TOKENS_FILE" ]; then
    EXISTING=$(jq 'length' "$TOKENS_FILE")
    if [ "$EXISTING" -ge "$TOTAL_APPS" ]; then
        echo "All $EXISTING tokens already captured — skipping."
    else
        echo "$EXISTING/$TOTAL_APPS tokens exist. Capturing remaining..."
        python3 "$SCRIPTS/capture-tokens-auto.py"
    fi
else
    echo "No tokens found. Starting automated capture..."
    echo "A browser will open. You may need to log in once."
    echo ""
    python3 "$SCRIPTS/capture-tokens-auto.py"
fi

# Verify
if [ ! -f "$TOKENS_FILE" ]; then
    echo "ERROR: tokens.json not created. Aborting."
    exit 1
fi
CAPTURED=$(jq 'length' "$TOKENS_FILE")
echo ""
echo "Tokens: $CAPTURED/$TOTAL_APPS captured"
echo ""

# -----------------------------------------------------------
# Step 3: Parallel seed
# -----------------------------------------------------------
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " STEP 3/3: Seed data + themes (5 stores parallel)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

python3 "$SCRIPTS/seed-parallel.py" --concurrency 5 --orders 75

echo ""
echo "  ╔═══════════════════════════════════════════════╗"
echo "  ║  ALL DONE                                     ║"
echo "  ║                                               ║"
echo "  ║  $CAPTURED stores configured with:              "
echo "  ║  • 25 products (5 collections)                ║"
echo "  ║  • 30 customers                               ║"
echo "  ║  • 10 discount codes                          ║"
echo "  ║  • 75 UTM-attributed orders                   ║"
echo "  ║  • Dawn theme (live)                          ║"
echo "  ║                                               ║"
echo "  ║  Tokens: scripts/tokens.json                  ║"
echo "  ╚═══════════════════════════════════════════════╝"
echo ""
