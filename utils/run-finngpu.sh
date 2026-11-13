#!/bin/bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
WWW_DIR="${WWW_DIR:-/var/www/finngpu}"
DATA_DIR="$WWW_DIR/data"
PUBLIC_DIR="$WWW_DIR/public"

for dependency in uv curl; do
    if ! command -v "$dependency" >/dev/null 2>&1; then
        echo "Error: '$dependency' is required." >&2
        exit 1
    fi
done

cd "$BASE_DIR"
mkdir -p "$DATA_DIR" "$PUBLIC_DIR"
umask 002

run_python() {
    uv run --no-project --with requests --with beautifulsoup4 --with pandas --with tqdm python "$@"
}

LISTINGS="$DATA_DIR/finn_gpu_listings_$(date +%F).csv"
run_python finngpu.py --output "$LISTINGS"
run_python retail_prices.py --output "$DATA_DIR/retail_prices.json"
run_python historical_prices.py --retail "$DATA_DIR/retail_prices.json" \
    --output "$DATA_DIR/historical_prices.json"
run_python price_analysis.py --file "$LISTINGS" \
    --retail "$DATA_DIR/retail_prices.json" \
    --history "$DATA_DIR/historical_prices.json" --csv "$DATA_DIR/deals.csv"
run_python -m utils.generate_results --csv "$DATA_DIR/deals.csv" \
    --output "$PUBLIC_DIR/index.html"
