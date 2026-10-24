#!/usr/bin/env bash
# ==============================================================================
# Sparkian Suite - Automated Dependency Installer
# Installs Python dependencies, OS browser libraries, and Camoufox binaries.
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== [1/4] Checking System & Package Manager ==="
if [ -f /etc/debian_version ]; then
    echo "Detected Debian/Ubuntu system."
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -y
    apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        python3-venv \
        curl \
        sqlite3 \
        libgtk-3-0 \
        libasound2 \
        libx11-xcb1 \
        libdbus-glib-1-2 \
        libxt6 \
        libxcomposite1 \
        libxdamage1 \
        libxrandr2 \
        libgbm1 \
        libpango-1.0-0 \
        libatk1.0-0 \
        libatk-bridge2.0-0 \
        ca-certificates \
        fonts-liberation
else
    echo "Non-Debian system detected. Please ensure Python 3.10+ and Firefox libraries are installed."
fi

echo "=== [2/4] Installing Python Requirements ==="
python3 -m pip install --upgrade pip setuptools wheel
python3 -m pip install -r requirements.txt

echo "=== [3/4] Fetching Camoufox Anti-Detect Browser Binaries ==="
python3 -m camoufox fetch

echo "=== [4/4] Verifying Installation ==="
python3 -c "
import camoufox
from camoufox.async_api import AsyncCamoufox
from tempmail import TempikClient
print('✓ Camoufox & Tempik successfully loaded!')
"

chmod +x sparkian_camoufox_farm.py sparkian_adapter.py

echo "=============================================================="
echo "✓ Sparkian Suite installation completed successfully!"
echo ""
echo "Usage:"
echo "  1. Run account farm generator:"
echo "     python3 sparkian_camoufox_farm.py -n 5"
echo ""
echo "  2. Start OpenAI-compatible adapter for 9router:"
echo "     python3 sparkian_adapter.py --port 20128"
echo "=============================================================="
