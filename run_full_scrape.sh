#!/bin/bash
# Full SAQ scrape runner

cd "$(dirname "$0")"
source venv/bin/activate

echo "=========================================="
echo "SAQ FULL CATALOG SCRAPE"
echo "=========================================="
echo ""

# Phase 1: Scan catalog
echo "PHASE 1: Scanning catalog for product codes..."
python catalog_scanner.py

if [ ! -f product_codes.json ]; then
    echo "ERROR: product_codes.json not created"
    exit 1
fi

TOTAL=$(python -c "import json; print(len(json.load(open('product_codes.json'))))")
echo ""
echo "Found $TOTAL products"
echo ""

# Phase 2: Parallel inventory scrape
echo "PHASE 2: Parallel inventory scrape..."
echo "Using 30 workers"
echo ""

python parallel_scraper.py --workers 30 --output output

echo ""
echo "=========================================="
echo "SCRAPE COMPLETE"
echo "=========================================="
echo "Results in: output/full_inventory.json"
