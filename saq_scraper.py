#!/usr/bin/env python3
"""
SAQ Inventory Scraper

Scrapes product inventory availability across SAQ store locations
using Playwright for browser automation.

Usage:
    python saq_scraper.py <product_url> [--location <store_name_pattern>] [--all] [--output FILE]

Examples:
    # Get inventory for a product (loads ~50 stores by default)
    python saq_scraper.py https://www.saq.com/fr/10510354

    # Filter to stores matching "Beaubien" in the name
    python saq_scraper.py https://www.saq.com/fr/10510354 --location "Beaubien"

    # Load ALL stores (slower, but complete data)
    python saq_scraper.py https://www.saq.com/fr/10510354 --all

    # Save to JSON file
    python saq_scraper.py https://www.saq.com/fr/10510354 --output inventory.json

    # Save to CSV file
    python saq_scraper.py https://www.saq.com/fr/10510354 --output inventory.csv

Note: The --location flag filters by store NAME pattern (case-insensitive).
      Montreal stores use neighborhood names (Beaubien, Laurier, Rosemont, etc.)
"""

import asyncio
import argparse
import csv
import json
import sys
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
except ImportError:
    print("Playwright not installed. Run: pip install playwright && playwright install chromium")
    sys.exit(1)


@dataclass
class StoreInventory:
    """Represents inventory at a single store."""
    store_name: str
    store_id: str
    quantity: int
    address: Optional[str] = None
    distance: Optional[str] = None


@dataclass
class ProductInfo:
    """Product metadata."""
    name: str
    code: str
    price: str
    url: str


class SAQScraper:
    """Scrapes inventory data from SAQ product pages."""

    def __init__(self, headless: bool = True, timeout: int = 60000):
        self.headless = headless
        self.timeout = timeout
        self.browser = None
        self.context = None

    async def __aenter__(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=self.headless)
        self.context = await self.browser.new_context(
            viewport={"width": 1920, "height": 1080},
            locale="fr-CA",
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()

    async def get_product_info(self, page) -> ProductInfo:
        """Extract product information from the page."""
        # Wait for page to fully load
        await page.wait_for_load_state("networkidle")

        # Get product name
        name_el = page.locator("h1.page-title")
        name = await name_el.text_content(timeout=10000) if await name_el.count() > 0 else ""

        # Get price
        price_el = page.locator("span.price").first
        price = await price_el.text_content(timeout=5000) if await price_el.count() > 0 else ""

        # Extract SAQ code from URL (most reliable)
        url = page.url
        code = url.rstrip("/").split("/")[-1] if "/" in url else ""

        return ProductInfo(
            name=name.strip() if name else "",
            code=code.strip() if code else "",
            price=price.strip() if price else "",
            url=url
        )

    async def get_online_stock(self, page) -> int:
        """Get online availability quantity."""
        try:
            online_qty = await page.locator(
                ".stock-label-container .product-online-availability span"
            ).first.text_content(timeout=5000)
            return int(online_qty.strip()) if online_qty else 0
        except (PlaywrightTimeout, ValueError):
            return 0

    async def open_store_panel(self, page):
        """Open the store availability panel."""
        # Click on "Disponibilité en succursale" button
        toggle_btn = page.locator("div.available-in-store button.action.toggle")
        await toggle_btn.wait_for(state="visible", timeout=self.timeout)
        await toggle_btn.click()

        # Wait for store list to load
        await page.locator("ul.store-list li").first.wait_for(
            state="visible", timeout=self.timeout
        )

    async def set_location(self, page, location: str):
        """Set location filter in the store panel.

        Note: SAQ's off-canvas store panel doesn't have a visible search input.
        This method is kept for potential future use or if the site changes.
        Filtering is done post-scrape via the filter_inventory function.
        """
        # The off-canvas panel search is not easily accessible
        # Location filtering is handled post-scrape
        pass

    async def load_more_stores(self, page, max_clicks: int = 50):
        """Click 'Show more' to load more stores.

        Args:
            page: Playwright page
            max_clicks: Maximum number of times to click show more (0 = until exhausted)
        """
        show_more_selector = "div.list-footer button.action, button.action.primary:has-text('plus')"
        clicks = 0

        while max_clicks == 0 or clicks < max_clicks:
            show_more = page.locator(show_more_selector)
            try:
                if await show_more.count() == 0:
                    break
                if not await show_more.first.is_visible():
                    break

                current_count = await page.locator("ul.store-list li").count()
                await show_more.first.click()
                clicks += 1

                # Wait for more items to load
                await page.wait_for_function(
                    f"document.querySelectorAll('ul.store-list li').length > {current_count}",
                    timeout=10000
                )
            except (PlaywrightTimeout, Exception):
                break

        return clicks

    async def extract_store_inventory(self, page) -> list[StoreInventory]:
        """Extract inventory data from all visible stores."""
        inventory = []

        store_items = page.locator("ul.store-list li")
        count = await store_items.count()

        for i in range(count):
            item = store_items.nth(i)
            try:
                # Store name (try multiple selectors)
                name = ""
                for name_sel in [".name h4", "h4"]:
                    name_el = item.locator(name_sel)
                    if await name_el.count() > 0:
                        name = await name_el.first.text_content()
                        break

                # Store ID
                id_el = item.locator("span[data-bind='text: id']")
                store_id = await id_el.text_content() if await id_el.count() > 0 else ""

                # Quantity
                qty_text = "0"
                for qty_sel in [".disponibility strong", "strong[data-bind*='qty']"]:
                    qty_el = item.locator(qty_sel)
                    if await qty_el.count() > 0:
                        qty_text = await qty_el.first.text_content()
                        break

                # Address (optional)
                address = None

                # Distance (optional)
                dist_el = item.locator("span.distance")
                distance = await dist_el.text_content() if await dist_el.count() > 0 else None

                inventory.append(StoreInventory(
                    store_name=name.strip() if name else f"Store {i+1}",
                    store_id=store_id.strip() if store_id else "",
                    quantity=int(qty_text.strip()) if qty_text else 0,
                    address=address.strip() if address else None,
                    distance=distance.strip() if distance else None,
                ))
            except Exception as e:
                print(f"Warning: Could not parse store {i}: {e}", file=sys.stderr)
                continue

        return inventory

    async def scrape(
        self,
        product_url: str,
        location: Optional[str] = None,
        load_all: bool = False
    ) -> tuple[ProductInfo, list[StoreInventory]]:
        """
        Scrape inventory for a product.

        Args:
            product_url: SAQ product page URL
            location: Optional location filter (postal code or city)
            load_all: If True, load all stores (slower)

        Returns:
            Tuple of (ProductInfo, list of StoreInventory)
        """
        page = await self.context.new_page()

        try:
            print(f"Loading {product_url}...", file=sys.stderr)
            await page.goto(product_url, wait_until="domcontentloaded")

            # Get product info
            product = await self.get_product_info(page)
            print(f"Product: {product.name}", file=sys.stderr)

            # Get online stock
            online_qty = await self.get_online_stock(page)

            # Open store availability panel
            print("Opening store availability...", file=sys.stderr)
            await self.open_store_panel(page)

            # Set location filter if provided
            if location:
                print(f"Filtering by location: {location}", file=sys.stderr)
                await self.set_location(page, location)

            # Load more stores (default: load a reasonable amount, --all loads everything)
            if load_all:
                print("Loading all stores...", file=sys.stderr)
                await self.load_more_stores(page, max_clicks=0)  # 0 = load all
            else:
                # Load a few more pages by default to get ~50 stores
                await self.load_more_stores(page, max_clicks=5)

            # Extract inventory data
            inventory = await self.extract_store_inventory(page)

            # Add online stock as first entry
            inventory.insert(0, StoreInventory(
                store_name="En ligne / Online",
                store_id="0",
                quantity=online_qty
            ))

            return product, inventory

        finally:
            await page.close()


def filter_inventory(
    inventory: list[StoreInventory],
    location: Optional[str] = None
) -> list[StoreInventory]:
    """Filter inventory by store name pattern (case-insensitive)."""
    if not location:
        return inventory

    location_lower = location.lower()
    filtered = []

    for inv in inventory:
        # Always include online stock
        if inv.store_id == "0":
            filtered.append(inv)
            continue

        # Check if location matches store name
        if location_lower in inv.store_name.lower():
            filtered.append(inv)

    return filtered


def format_table(product: ProductInfo, inventory: list[StoreInventory]) -> str:
    """Format inventory as a readable table."""
    lines = []
    lines.append("=" * 70)
    lines.append(f"Product: {product.name}")
    lines.append(f"Code SAQ: {product.code}")
    lines.append(f"Price: {product.price}")
    lines.append("=" * 70)
    lines.append("")
    lines.append(f"{'Store':<40} {'ID':<10} {'Qty':>8}")
    lines.append("-" * 60)

    total = 0
    for inv in inventory:
        total += inv.quantity
        name = inv.store_name[:38] if len(inv.store_name) > 38 else inv.store_name
        lines.append(f"{name:<40} {inv.store_id:<10} {inv.quantity:>8}")

    lines.append("-" * 60)
    lines.append(f"{'TOTAL':<40} {'':<10} {total:>8}")
    lines.append("")

    return "\n".join(lines)


def save_csv(
    product: ProductInfo,
    inventory: list[StoreInventory],
    filename: str
):
    """Save inventory to CSV file."""
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Product", product.name])
        writer.writerow(["Code SAQ", product.code])
        writer.writerow(["Price", product.price])
        writer.writerow(["Scraped", datetime.now().isoformat()])
        writer.writerow([])
        writer.writerow(["Store Name", "Store ID", "Quantity", "Address", "Distance"])

        for inv in inventory:
            writer.writerow([
                inv.store_name,
                inv.store_id,
                inv.quantity,
                inv.address or "",
                inv.distance or ""
            ])

        total = sum(inv.quantity for inv in inventory)
        writer.writerow(["TOTAL", "", total, "", ""])


def save_json(
    product: ProductInfo,
    inventory: list[StoreInventory],
    filename: str
):
    """Save inventory to JSON file."""
    data = {
        "product": asdict(product),
        "scraped_at": datetime.now().isoformat(),
        "inventory": [asdict(inv) for inv in inventory],
        "total_quantity": sum(inv.quantity for inv in inventory)
    }
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


async def main():
    parser = argparse.ArgumentParser(
        description="Scrape SAQ product inventory by store location"
    )
    parser.add_argument(
        "url",
        help="SAQ product URL (e.g., https://www.saq.com/fr/10510354)"
    )
    parser.add_argument(
        "--location", "-l",
        help="Filter by store name pattern (case-insensitive, e.g., 'Beaubien', 'Rosemont')"
    )
    parser.add_argument(
        "--all", "-a",
        action="store_true",
        dest="load_all",
        help="Load all stores (slower, gets complete inventory)"
    )
    parser.add_argument(
        "--output", "-o",
        help="Output file (supports .csv and .json)"
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        default=True,
        help="Run browser in headless mode (default)"
    )
    parser.add_argument(
        "--no-headless",
        action="store_false",
        dest="headless",
        help="Show browser window (useful for debugging)"
    )

    args = parser.parse_args()

    async with SAQScraper(headless=args.headless) as scraper:
        product, inventory = await scraper.scrape(
            args.url,
            location=None,  # Location filtering done post-scrape
            load_all=args.load_all
        )

    # Filter by location if specified
    if args.location:
        inventory = filter_inventory(inventory, args.location)

    # Print table
    print(format_table(product, inventory))

    # Save to file if requested
    if args.output:
        output_path = Path(args.output)
        if output_path.suffix.lower() == ".json":
            save_json(product, inventory, args.output)
        else:
            save_csv(product, inventory, args.output)
        print(f"Saved to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
