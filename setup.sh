#!/bin/bash
# ─────────────────────────────────────────────────────────
#  Towbook Automation — VPS Setup Script
#  Run this once after uploading files to your VPS
# ─────────────────────────────────────────────────────────

set -e

echo ""
echo "========================================"
echo "  Towbook Automation Setup"
echo "========================================"
echo ""

# ── Create .env from template ────────────────────────────
if [ ! -f .env ]; then
  echo "Setting up your .env file..."
  echo ""
  read -p "  Towbook username (email): " TB_USER
  read -s -p "  Towbook password: " TB_PASS
  echo ""
  read -p "  n8n admin password (make something up): " N8N_PASS
  echo ""

  cat > .env <<EOF
TOWBOOK_USERNAME=${TB_USER}
TOWBOOK_PASSWORD=${TB_PASS}
N8N_BASIC_AUTH_USER=admin
N8N_BASIC_AUTH_PASSWORD=${N8N_PASS}
EOF

  echo "✅ .env created"
else
  echo "✅ .env already exists, skipping"
fi

# ── Build and start containers ───────────────────────────
echo ""
echo "Building and starting containers (this takes ~2 mins first time)..."
docker compose up -d --build

# ── Wait for containers ──────────────────────────────────
echo ""
echo "Waiting for scraper to be ready..."
sleep 10

# ── Health check ─────────────────────────────────────────
STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:5050/health)
if [ "$STATUS" = "200" ]; then
  echo "✅ Scraper is running"
else
  echo "⚠️  Scraper not responding yet — check: docker logs towbook-scraper"
fi

echo ""
echo "========================================"
echo "  All done!"
echo ""
echo "  n8n UI:  http://$(curl -s ifconfig.me):5678"
echo "  Login:   admin / (password you just set)"
echo ""
echo "  Next steps:"
echo "  1. Open n8n UI in your browser"
echo "  2. Go to Workflows → Import → upload n8n_workflow.json"
echo "  3. Activate the workflow"
echo ""
echo "  To test scraper manually:"
echo "  curl -X POST http://localhost:5050/scrape"
echo ""
echo "  To watch logs:"
echo "  docker logs towbook-scraper -f"
echo "========================================"
