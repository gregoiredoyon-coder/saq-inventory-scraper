#!/usr/bin/env python3
"""
Phase 1: Scan SAQ catalog to get all product URLs.
Fast scan, no inventory - just collect product codes.
"""

import asyncio
import json
from datetime import datetime
from playwright.async_api import async_playwright


CATEGORIES = [
    ("vin-rouge", "https://www.saq.com/fr/produits/vin/vin-rouge"),
    ("vin-blanc", "https://www.saq.com/fr/produits/vin/vin-blanc"),
    ("vin-rose", "https://www.saq.com/fr/produits/vin/vin-rose"),
    ("vin-mousseux", "https://www.saq.com/fr/produits/vin/vin-mousseux"),
    ("vin-dessert", "https://www.saq.com/fr/produits/vin/vin-de-dessert-et-de-porto"),
    ("spiritueux", "https://www.saq.com/fr/produits/spiritueux"),
    ("biere", "https://www.saq.com/fr/produits/biere"),
    ("cidre", "https://www.saq.com/fr/produits/cidre"),
    ("sans-alcool", "https://www.saq.com/fr/produits/sans-alcool"),
]


async def get_products_from_page(page) -> list[dict]:
    """Extract product info from current page."""
    products = []

    items = page.locator(".product-item")
    count = await items.count()

    for i in range(count):
        item = items.nth(i)
        try:
            # Get product link (try multiple selectors)
            href = None
            name = ""

            for link_sel in ["a.product-item-link", "a.product-item-photo", ".product-item-info a"]:
                link = item.locator(link_sel)
                if await link.count() > 0:
                    href = await link.first.get_attribute("href")
                    if href and "saq.com" in href:
                        # Normalize URL
                        if href.startswith("//"):
                            href = "https:" + href
                        break

            # Get name separately
            name_el = item.locator(".product-item-link, .product-item-name")
            if await name_el.count() > 0:
                name = await name_el.first.text_content()

            if href:

                # Extract code from URL
                code = href.rstrip("/").split("/")[-1] if href else ""

                # Get price
                price_el = item.locator("span.price")
                price = await price_el.first.text_content() if await price_el.count() > 0 else ""

                if code and code.isdigit():
                    products.append({
                        "code": code,
                        "name": name.strip() if name else "",
                        "price": price.strip() if price else "",
                        "url": href
                    })
        except Exception as e:
            continue

    return products


async def scan_category(browser, category_name: str, base_url: str) -> list[dict]:
    """Scan all pages of a category."""
    page = await browser.new_page()
    all_products = []
    page_num = 1

    try:
        while True:
            url = f"{base_url}?product_list_limit=96&p={page_num}"
            print(f"  [{category_name}] Page {page_num}: {url}")

            await page.goto(url, wait_until="domcontentloaded", timeout=60000)

            try:
                await page.wait_for_selector(".product-item", timeout=15000)
            except:
                # No products found, might be end
                break

            await page.wait_for_timeout(1500)

            products = await get_products_from_page(page)

            if not products:
                break

            all_products.extend(products)
            print(f"  [{category_name}] Found {len(products)} products (total: {len(all_products)})")

            # Check if there's a next page
            next_btn = page.locator("a.action.next, li.pages-item-next a")
            if await next_btn.count() == 0:
                break

            # Check if next button is disabled/hidden
            try:
                is_visible = await next_btn.first.is_visible()
                if not is_visible:
                    break
            except:
                break

            page_num += 1

            # Safety limit
            if page_num > 100:
                print(f"  [{category_name}] Hit page limit")
                break

    finally:
        await page.close()

    return all_products


async def scan_full_catalog(max_concurrent: int = 3) -> list[dict]:
    """Scan entire SAQ catalog."""
    print("=" * 60)
    print("SAQ CATALOG SCANNER")
    print("=" * 60)
    print(f"Scanning {len(CATEGORIES)} categories...")
    print()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        all_products = []
        seen_codes = set()

        # Scan categories with limited concurrency
        semaphore = asyncio.Semaphore(max_concurrent)

        async def scan_with_limit(cat_name, url):
            async with semaphore:
                return await scan_category(browser, cat_name, url)

        tasks = [scan_with_limit(name, url) for name, url in CATEGORIES]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for (cat_name, _), result in zip(CATEGORIES, results):
            if isinstance(result, Exception):
                print(f"  [{cat_name}] ERROR: {result}")
                continue

            for product in result:
                if product["code"] not in seen_codes:
                    seen_codes.add(product["code"])
                    product["category"] = cat_name
                    all_products.append(product)

        await browser.close()

    print()
    print("=" * 60)
    print(f"TOTAL UNIQUE PRODUCTS: {len(all_products)}")
    print("=" * 60)

    return all_products


def save_catalog(products: list[dict], filename: str = "catalog.json"):
    """Save catalog to JSON file."""
    data = {
        "scanned_at": datetime.now().isoformat(),
        "total_products": len(products),
        "products": products
    }

    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"Saved to {filename}")


async def main():
    products = await scan_full_catalog(max_concurrent=3)
    save_catalog(products)

    # Also save just the codes for easy splitting
    codes = [p["code"] for p in products]
    with open("product_codes.json", "w") as f:
        json.dump(codes, f)

    print(f"Saved {len(codes)} product codes to product_codes.json")


if __name__ == "__main__":
    asyncio.run(main())
