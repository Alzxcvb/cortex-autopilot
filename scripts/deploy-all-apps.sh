#!/usr/bin/env bash
#
# Deploy baseline app config to all 30 hackathon apps.
# Sets each app to: localhost:3456 redirect, wide scopes, no webhooks.
#
# Usage:
#   cd HackathonStarterRepo
#   bash scripts/deploy-all-apps.sh
#
# Prerequisites:
#   - shopify CLI installed and logged in (shopify auth login)
#   - scripts/apps.json exists with all 30 app credentials
#   - jq installed (sudo apt install jq)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APPS_FILE="$REPO_ROOT/scripts/apps.json"
TOML_FILE="$REPO_ROOT/shopify.app.toml"
TOML_BACKUP="$REPO_ROOT/shopify.app.toml.bak"

if [ ! -f "$APPS_FILE" ]; then
    echo "ERROR: apps.json not found at $APPS_FILE"
    exit 1
fi

# Check jq is available
if ! command -v jq &>/dev/null; then
    echo "ERROR: jq is required. Install: sudo apt install jq"
    exit 1
fi

# Backup original TOML
cp "$TOML_FILE" "$TOML_BACKUP"

SCOPES="read_products,write_products,read_orders,write_orders,read_customers,write_customers,read_inventory,write_inventory,read_fulfillments,write_fulfillments,read_analytics,read_themes,write_themes,read_script_tags,write_script_tags,read_content,write_content,read_price_rules,write_price_rules,read_discounts,write_discounts,read_draft_orders,write_draft_orders,read_locations,read_files,write_files"

TOTAL=$(jq 'length' "$APPS_FILE")
CURRENT=0
FAILED=0

echo "============================================="
echo " Deploying baseline config to $TOTAL apps"
echo "============================================="
echo ""

for APP_NAME in $(jq -r 'keys[]' "$APPS_FILE" | sort); do
    CURRENT=$((CURRENT + 1))
    CLIENT_ID=$(jq -r ".\"$APP_NAME\".client_id" "$APPS_FILE")

    echo "[$CURRENT/$TOTAL] $APP_NAME (client_id: ${CLIENT_ID:0:12}...)"

    # Write TOML for this app
    cat > "$TOML_FILE" <<EOF
name = "$APP_NAME"
client_id = "$CLIENT_ID"
application_url = "http://localhost:3456"
embedded = false

[access_scopes]
scopes = "$SCOPES"

[auth]
redirect_urls = ["http://localhost:3456/auth/callback"]

[webhooks]
api_version = "2025-04"
EOF

    # Deploy
    if shopify app deploy --force --path "$REPO_ROOT" --client-id "$CLIENT_ID" 2>&1; then
        echo "  ✓ $APP_NAME deployed"
    else
        echo "  ✗ $APP_NAME FAILED"
        FAILED=$((FAILED + 1))
    fi

    echo ""
    sleep 1  # Rate limit safety
done

# Restore original TOML
cp "$TOML_BACKUP" "$TOML_FILE"
rm "$TOML_BACKUP"

echo "============================================="
echo " Done: $((TOTAL - FAILED))/$TOTAL succeeded"
if [ $FAILED -gt 0 ]; then
    echo " $FAILED FAILED — check output above"
fi
echo "============================================="
