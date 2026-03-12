#!/usr/bin/env python3
"""
Phase 2: Parallel inventory scraper.
Split catalog across N workers for fast scraping.
"""

import asyncio
import json
import sys
import os
from datetime import datetime
from pathlib import Path
from typing import Optional
import argparse

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout


class InventoryWorker:
    """Single worker that scrapes inventory for assigned products."""

    def __init__(self, worker_id: int, headless: bool = True):
        self.worker_id = worker_id
        self.headless = headless
        self.results = []
        self.errors = []

    async def scrape_product(self, page, code: str) -> Optional[dict]:
        """Scrape inventory for a single product."""
        url = f"https://www.saq.com/fr/{code}"

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_load_state("networkidle", timeout=15000)

            # Get product name
            name_el = page.locator("h1.page-title")
            name = await name_el.text_content(timeout=5000) if await name_el.count() > 0 else ""

            # Get price
            price_el = page.locator("span.price").first
            price = await price_el.text_content(timeout=5000) if await price_el.count() > 0 else ""

            # Get online stock
            online_qty = 0
            try:
                online_el = page.locator(".stock-label-container .product-online-availability span")
                if await online_el.count() > 0:
                    qty_text = await online_el.first.text_content(timeout=3000)
                    online_qty = int(qty_text.strip()) if qty_text else 0
            except:
                pass

            # Open store panel
            toggle = page.locator("div.available-in-store button.action.toggle")
            if await toggle.count() > 0 and await toggle.is_visible():
                await toggle.click()
                await page.wait_for_selector("ul.store-list li", timeout=15000)
                await page.wait_for_timeout(1000)

                # Load more stores (5 clicks to get ~60 stores)
                for _ in range(5):
                    show_more = page.locator("div.list-footer button.action")
                    try:
                        if await show_more.count() > 0 and await show_more.first.is_visible():
                            await show_more.first.click()
                            await page.wait_for_timeout(1500)
                    except:
                        break

                # Extract store inventory
                inventory = []
                stores = page.locator("ul.store-list li")
                count = await stores.count()

                for i in range(count):
                    store = stores.nth(i)
                    try:
                        store_name = ""
                        for sel in [".name h4", "h4"]:
                            el = store.locator(sel)
                            if await el.count() > 0:
                                store_name = await el.first.text_content()
                                break

                        store_id = ""
                        id_el = store.locator("span[data-bind='text: id']")
                        if await id_el.count() > 0:
                            store_id = await id_el.text_content()

                        qty = 0
                        for sel in [".disponibility strong", "strong[data-bind*='qty']"]:
                            el = store.locator(sel)
                            if await el.count() > 0:
                                qty_text = await el.first.text_content()
                                qty = int(qty_text.strip()) if qty_text else 0
                                break

                        if store_name:
                            inventory.append({
                                "store": store_name.strip(),
                                "id": store_id.strip() if store_id else "",
                                "qty": qty
                            })
                    except:
                        continue

                return {
                    "code": code,
                    "name": name.strip() if name else "",
                    "price": price.strip() if price else "",
                    "online_qty": online_qty,
                    "stores": inventory,
                    "total_qty": online_qty + sum(s["qty"] for s in inventory)
                }
            else:
                # No store panel, just return basic info
                return {
                    "code": code,
                    "name": name.strip() if name else "",
                    "price": price.strip() if price else "",
                    "online_qty": online_qty,
                    "stores": [],
                    "total_qty": online_qty
                }

        except Exception as e:
            return None

    async def run(self, product_codes: list[str], output_file: str):
        """Run worker on assigned product codes."""
        print(f"[Worker {self.worker_id}] Starting with {len(product_codes)} products")

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=self.headless)
            context = await browser.new_context(viewport={"width": 1920, "height": 1080})
            page = await context.new_page()

            for i, code in enumerate(product_codes):
                try:
                    result = await self.scrape_product(page, code)
                    if result:
                        self.results.append(result)
                        print(f"[Worker {self.worker_id}] {i+1}/{len(product_codes)} - {code}: {result['name'][:30]}... ({result['total_qty']} total)")
                    else:
                        self.errors.append({"code": code, "error": "Failed to scrape"})
                        print(f"[Worker {self.worker_id}] {i+1}/{len(product_codes)} - {code}: FAILED")
                except Exception as e:
                    self.errors.append({"code": code, "error": str(e)})
                    print(f"[Worker {self.worker_id}] {i+1}/{len(product_codes)} - {code}: ERROR - {e}")

                # Small delay to be nice to SAQ servers
                await asyncio.sleep(0.5)

            await browser.close()

        # Save results
        data = {
            "worker_id": self.worker_id,
            "completed_at": datetime.now().isoformat(),
            "total_scraped": len(self.results),
            "total_errors": len(self.errors),
            "results": self.results,
            "errors": self.errors
        }

        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        print(f"[Worker {self.worker_id}] Done! Saved to {output_file}")
        return self.results


def split_list(lst: list, n: int) -> list[list]:
    """Split list into n roughly equal chunks."""
    k, m = divmod(len(lst), n)
    return [lst[i * k + min(i, m):(i + 1) * k + min(i + 1, m)] for i in range(n)]


async def run_parallel_scrape(
    product_codes: list[str],
    num_workers: int = 30,
    output_dir: str = "output"
):
    """Run parallel scrape with multiple workers."""
    print("=" * 60)
    print(f"PARALLEL SAQ SCRAPER")
    print("=" * 60)
    print(f"Products: {len(product_codes)}")
    print(f"Workers: {num_workers}")
    print(f"Products per worker: ~{len(product_codes) // num_workers}")
    print("=" * 60)

    # Create output directory
    Path(output_dir).mkdir(exist_ok=True)

    # Split products among workers
    chunks = split_list(product_codes, num_workers)

    # Create and run workers
    tasks = []
    for i, chunk in enumerate(chunks):
        if chunk:  # Skip empty chunks
            worker = InventoryWorker(worker_id=i)
            output_file = f"{output_dir}/worker_{i:02d}.json"
            tasks.append(worker.run(chunk, output_file))

    # Run all workers in parallel
    start_time = datetime.now()
    await asyncio.gather(*tasks)
    end_time = datetime.now()

    print()
    print("=" * 60)
    print(f"ALL WORKERS COMPLETE")
    print(f"Duration: {end_time - start_time}")
    print("=" * 60)

    # Merge results
    merge_results(output_dir)


def merge_results(output_dir: str):
    """Merge all worker results into single file."""
    all_results = []
    all_errors = []

    for f in sorted(Path(output_dir).glob("worker_*.json")):
        with open(f) as fp:
            data = json.load(fp)
            all_results.extend(data.get("results", []))
            all_errors.extend(data.get("errors", []))

    merged = {
        "merged_at": datetime.now().isoformat(),
        "total_products": len(all_results),
        "total_errors": len(all_errors),
        "results": all_results,
        "errors": all_errors
    }

    output_file = f"{output_dir}/full_inventory.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2, ensure_ascii=False)

    print(f"Merged {len(all_results)} products to {output_file}")


async def main():
    parser = argparse.ArgumentParser(description="Parallel SAQ inventory scraper")
    parser.add_argument("--codes", default="product_codes.json", help="JSON file with product codes")
    parser.add_argument("--workers", type=int, default=30, help="Number of parallel workers")
    parser.add_argument("--output", default="output", help="Output directory")
    parser.add_argument("--start", type=int, default=0, help="Start index (for resuming)")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of products (0=all)")

    args = parser.parse_args()

    # Load product codes
    with open(args.codes) as f:
        codes = json.load(f)

    # Apply start/limit
    if args.start > 0:
        codes = codes[args.start:]
    if args.limit > 0:
        codes = codes[:args.limit]

    print(f"Loaded {len(codes)} product codes")

    await run_parallel_scrape(codes, args.workers, args.output)


if __name__ == "__main__":
    asyncio.run(main())
