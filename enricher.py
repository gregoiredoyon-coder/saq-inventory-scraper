#!/usr/bin/env python3
"""
SAQ + Vivino Enricher - Combine SAQ inventory with Vivino ratings.
"""

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, asdict

from vivino_scraper import VivinoScraper, WineMatcher, VivinoWine


@dataclass
class EnrichedProduct:
    """SAQ product enriched with Vivino data."""
    # SAQ data
    code: str
    name: str
    price: str
    category: str
    url: str

    # Vivino data
    vivino_rating: Optional[float] = None
    vivino_reviews: Optional[int] = None
    vivino_name: Optional[str] = None
    vivino_winery: Optional[str] = None
    vivino_region: Optional[str] = None
    vivino_country: Optional[str] = None
    vivino_grape: Optional[str] = None
    vivino_url: Optional[str] = None
    match_score: Optional[float] = None

    # Computed
    value_score: Optional[float] = None  # Rating per dollar


class SAQVivinoEnricher:
    """Enrich SAQ catalog with Vivino ratings."""

    def __init__(self, headless: bool = True):
        self.headless = headless
        self.stats = {
            "total": 0,
            "matched": 0,
            "unmatched": 0,
            "errors": 0,
        }

    async def enrich_catalog(
        self,
        catalog_file: str = "catalog.json",
        output_file: str = "enriched_catalog.json",
        limit: int = 0,
        min_match_score: float = 0.4,
    ):
        """Enrich entire SAQ catalog with Vivino data."""
        print("=" * 70)
        print("SAQ + VIVINO ENRICHER")
        print("=" * 70)

        # Load catalog
        with open(catalog_file) as f:
            catalog = json.load(f)

        products = catalog.get("products", [])
        if limit > 0:
            products = products[:limit]

        print(f"Loaded {len(products)} products from {catalog_file}")
        print()

        enriched = []

        async with VivinoScraper(headless=self.headless) as scraper:
            matcher = WineMatcher(scraper)

            for i, product in enumerate(products):
                self.stats["total"] += 1

                try:
                    result = await self._enrich_product(product, matcher, min_match_score)
                    enriched.append(result)

                    if result.vivino_rating:
                        self.stats["matched"] += 1
                        status = f"Rating: {result.vivino_rating:.1f}"
                    else:
                        self.stats["unmatched"] += 1
                        status = "No match"

                    print(f"[{i+1}/{len(products)}] {product['name'][:40]}... - {status}")

                except Exception as e:
                    self.stats["errors"] += 1
                    print(f"[{i+1}/{len(products)}] {product['name'][:40]}... - ERROR: {e}")

                    # Still add unmatched product
                    enriched.append(EnrichedProduct(
                        code=product.get("code", ""),
                        name=product.get("name", ""),
                        price=product.get("price", ""),
                        category=product.get("category", ""),
                        url=product.get("url", ""),
                    ))

                # Rate limiting
                await asyncio.sleep(0.3)

        # Calculate value scores
        self._calculate_value_scores(enriched)

        # Save results
        self._save_results(enriched, output_file)

        # Print summary
        self._print_summary()

        return enriched

    async def _enrich_product(
        self,
        product: dict,
        matcher: WineMatcher,
        min_match_score: float,
    ) -> EnrichedProduct:
        """Enrich a single product with Vivino data."""
        saq_name = product.get("name", "")

        # Find Vivino match
        match_result = await matcher.find_match(saq_name, min_match_score)

        enriched = EnrichedProduct(
            code=product.get("code", ""),
            name=saq_name,
            price=product.get("price", ""),
            category=product.get("category", ""),
            url=product.get("url", ""),
        )

        if match_result:
            wine, score = match_result
            enriched.vivino_rating = wine.rating
            enriched.vivino_reviews = wine.ratings_count
            enriched.vivino_name = wine.name
            enriched.vivino_winery = wine.winery
            enriched.vivino_region = wine.region
            enriched.vivino_country = wine.country
            enriched.vivino_grape = wine.grape
            enriched.vivino_url = wine.url
            enriched.match_score = score

        return enriched

    def _parse_price(self, price_str: str) -> Optional[float]:
        """Parse SAQ price string to float."""
        if not price_str:
            return None
        try:
            # Remove currency symbols and spaces
            cleaned = price_str.replace("$", "").replace(",", ".").replace(" ", "").strip()
            return float(cleaned)
        except:
            return None

    def _calculate_value_scores(self, products: list[EnrichedProduct]):
        """Calculate value score (rating per dollar) for each product."""
        for product in products:
            if product.vivino_rating and product.price:
                price = self._parse_price(product.price)
                if price and price > 0:
                    # Value score = rating / price * 10 (normalized)
                    product.value_score = round(product.vivino_rating / price * 10, 2)

    def _save_results(self, products: list[EnrichedProduct], output_file: str):
        """Save enriched products to JSON."""
        data = {
            "enriched_at": datetime.now().isoformat(),
            "total_products": len(products),
            "matched": self.stats["matched"],
            "unmatched": self.stats["unmatched"],
            "products": [asdict(p) for p in products],
        }

        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        print(f"\nSaved to {output_file}")

        # Also save top rated wines
        matched = [p for p in products if p.vivino_rating]
        top_rated = sorted(matched, key=lambda x: x.vivino_rating or 0, reverse=True)[:50]

        top_file = output_file.replace(".json", "_top50.json")
        with open(top_file, "w", encoding="utf-8") as f:
            json.dump({
                "top_rated": [asdict(p) for p in top_rated]
            }, f, indent=2, ensure_ascii=False)

        print(f"Saved top 50 to {top_file}")

        # Save best value wines
        with_value = [p for p in products if p.value_score]
        best_value = sorted(with_value, key=lambda x: x.value_score or 0, reverse=True)[:50]

        value_file = output_file.replace(".json", "_best_value.json")
        with open(value_file, "w", encoding="utf-8") as f:
            json.dump({
                "best_value": [asdict(p) for p in best_value]
            }, f, indent=2, ensure_ascii=False)

        print(f"Saved best value to {value_file}")

    def _print_summary(self):
        """Print enrichment summary."""
        print()
        print("=" * 70)
        print("ENRICHMENT SUMMARY")
        print("=" * 70)
        print(f"Total Products:  {self.stats['total']}")
        print(f"Matched:         {self.stats['matched']} ({self.stats['matched']/max(1,self.stats['total'])*100:.1f}%)")
        print(f"Unmatched:       {self.stats['unmatched']}")
        print(f"Errors:          {self.stats['errors']}")
        print("=" * 70)


async def enrich_from_inventory(
    inventory_file: str = "output/full_inventory.json",
    output_file: str = "output/enriched_inventory.json",
    limit: int = 0,
):
    """Enrich scraped inventory with Vivino data."""
    print("Loading inventory...")

    with open(inventory_file) as f:
        inventory = json.load(f)

    # Convert inventory format to catalog format
    products = []
    for item in inventory.get("results", []):
        products.append({
            "code": item.get("code", ""),
            "name": item.get("name", ""),
            "price": item.get("price", ""),
            "category": "",
            "url": f"https://www.saq.com/fr/{item.get('code', '')}",
        })

    # Save as temp catalog
    temp_catalog = {
        "products": products[:limit] if limit > 0 else products,
    }

    temp_file = "output/temp_catalog.json"
    with open(temp_file, "w") as f:
        json.dump(temp_catalog, f)

    # Run enricher
    enricher = SAQVivinoEnricher()
    await enricher.enrich_catalog(temp_file, output_file, limit=0)


async def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Enrich SAQ catalog with Vivino ratings")
    parser.add_argument("--catalog", default="catalog.json", help="Input catalog file")
    parser.add_argument("--output", default="enriched_catalog.json", help="Output file")
    parser.add_argument("--limit", type=int, default=0, help="Limit products (0=all)")
    parser.add_argument("--inventory", action="store_true", help="Use full inventory file")

    args = parser.parse_args()

    if args.inventory:
        await enrich_from_inventory(
            inventory_file="output/full_inventory.json",
            output_file=args.output,
            limit=args.limit,
        )
    else:
        enricher = SAQVivinoEnricher()
        await enricher.enrich_catalog(
            catalog_file=args.catalog,
            output_file=args.output,
            limit=args.limit,
        )


if __name__ == "__main__":
    asyncio.run(main())
