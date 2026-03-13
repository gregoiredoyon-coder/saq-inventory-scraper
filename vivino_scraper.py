#!/usr/bin/env python3
"""
Vivino Scraper - Get wine ratings using Playwright.
More reliable than API endpoints.
"""

import asyncio
import re
import json
from dataclasses import dataclass, asdict
from typing import Optional
from playwright.async_api import async_playwright, Page


@dataclass
class VivinoWine:
    """Vivino wine data."""
    name: str
    winery: str
    rating: float
    ratings_count: int
    region: str
    country: str
    url: str
    grape: Optional[str] = None
    price: Optional[float] = None


class VivinoScraper:
    """Scrape Vivino wine data using Playwright."""

    def __init__(self, headless: bool = True):
        self.headless = headless
        self._browser = None
        self._context = None

    async def __aenter__(self):
        playwright = await async_playwright().start()
        self._playwright = playwright
        self._browser = await playwright.chromium.launch(headless=self.headless)
        self._context = await self._browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def search(self, query: str, max_results: int = 5) -> list[VivinoWine]:
        """Search for wines on Vivino."""
        page = await self._context.new_page()

        try:
            # Navigate to search
            search_url = f"https://www.vivino.com/search/wines?q={query.replace(' ', '+')}"
            await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)

            # Wait for results
            try:
                await page.wait_for_selector("[class*='wineCard']", timeout=10000)
            except:
                # Try alternative selector
                try:
                    await page.wait_for_selector("[class*='wine-card']", timeout=5000)
                except:
                    return []

            await page.wait_for_timeout(2000)

            # Extract wines
            wines = await self._extract_wines(page, max_results)
            return wines

        except Exception as e:
            print(f"Search error for '{query}': {e}")
            return []
        finally:
            await page.close()

    async def _extract_wines(self, page: Page, max_results: int) -> list[VivinoWine]:
        """Extract wine data from search results page."""
        wines = []

        # Try multiple selector patterns
        selectors = [
            "[class*='wineCard']",
            "[class*='wine-card']",
            "[data-testid='wine-card']",
            ".search-results-list > div",
        ]

        items = None
        for sel in selectors:
            items = page.locator(sel)
            count = await items.count()
            if count > 0:
                break

        if not items or await items.count() == 0:
            return []

        count = min(await items.count(), max_results)

        for i in range(count):
            try:
                item = items.nth(i)

                # Get wine link and name
                link_el = item.locator("a").first
                href = await link_el.get_attribute("href") if await link_el.count() > 0 else ""
                url = f"https://www.vivino.com{href}" if href and not href.startswith("http") else href

                # Get full text and parse it
                full_text = await item.text_content()

                # Extract name (everything before the rating)
                name = ""
                # Get name (multiple possible selectors)
                for name_sel in ["[class*='name']", "span", "div"]:
                    name_el = item.locator(name_sel)
                    if await name_el.count() > 0:
                        name = await name_el.first.text_content()
                        if name and len(name) > 3:
                            break

                # Get rating (e.g., "3.7")
                rating = 0.0
                rating_match = re.search(r'(\d\.\d)', full_text or "")
                if rating_match:
                    try:
                        rating = float(rating_match.group(1))
                    except:
                        pass

                # Get review count (e.g., "2544 ratings" or "(2544 ratings)")
                reviews = 0
                reviews_match = re.search(r'\(?([\d,]+)\s*ratings?\)?', full_text or "", re.IGNORECASE)
                if reviews_match:
                    try:
                        reviews = int(reviews_match.group(1).replace(',', ''))
                    except:
                        pass

                # Get region/country
                region = ""
                country = ""
                region_el = item.locator("[class*='region'], [class*='location']")
                if await region_el.count() > 0:
                    region_text = await region_el.first.text_content()
                    parts = region_text.split(",") if region_text else []
                    if len(parts) >= 2:
                        region = parts[0].strip()
                        country = parts[-1].strip()
                    elif len(parts) == 1:
                        country = parts[0].strip()

                if name and rating > 0:
                    wines.append(VivinoWine(
                        name=name.strip(),
                        winery="",
                        rating=rating,
                        ratings_count=reviews,
                        region=region,
                        country=country,
                        url=url,
                    ))

            except Exception as e:
                continue

        return wines

    async def get_wine_details(self, url: str) -> Optional[VivinoWine]:
        """Get detailed info for a specific wine."""
        page = await self._context.new_page()

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2000)

            # Get name
            name_el = page.locator("[class*='wine-name'], h1")
            name = await name_el.first.text_content() if await name_el.count() > 0 else ""

            # Get winery
            winery_el = page.locator("[class*='winery'], [class*='producer']")
            winery = await winery_el.first.text_content() if await winery_el.count() > 0 else ""

            # Get rating
            rating = 0.0
            rating_el = page.locator("[class*='rating'] [class*='average']")
            if await rating_el.count() > 0:
                rating_text = await rating_el.first.text_content()
                try:
                    rating = float(re.search(r'[\d.]+', rating_text or "").group())
                except:
                    pass

            # Get review count
            reviews = 0
            reviews_el = page.locator("[class*='ratings'] [class*='count']")
            if await reviews_el.count() > 0:
                reviews_text = await reviews_el.first.text_content()
                try:
                    reviews = int(re.sub(r'[^\d]', '', reviews_text or "0"))
                except:
                    pass

            # Get region
            region_el = page.locator("[class*='region']")
            region_text = await region_el.first.text_content() if await region_el.count() > 0 else ""
            parts = region_text.split(",") if region_text else []
            region = parts[0].strip() if parts else ""
            country = parts[-1].strip() if len(parts) > 1 else ""

            # Get grape
            grape_el = page.locator("[class*='grape'], [class*='varietal']")
            grape = await grape_el.first.text_content() if await grape_el.count() > 0 else None

            return VivinoWine(
                name=name.strip() if name else "",
                winery=winery.strip() if winery else "",
                rating=rating,
                ratings_count=reviews,
                region=region,
                country=country,
                url=url,
                grape=grape.strip() if grape else None,
            )

        except Exception as e:
            print(f"Error getting details: {e}")
            return None
        finally:
            await page.close()


class WineMatcher:
    """Match SAQ wines to Vivino wines."""

    def __init__(self, scraper: VivinoScraper):
        self.scraper = scraper

    async def find_match(
        self,
        saq_name: str,
        min_similarity: float = 0.3,
    ) -> Optional[tuple[VivinoWine, float]]:
        """Find best Vivino match for a SAQ wine."""
        # Search Vivino
        results = await self.scraper.search(saq_name, max_results=5)

        if not results:
            # Try shorter query (first 2-3 words)
            words = saq_name.split()[:3]
            short_query = ' '.join(words)
            results = await self.scraper.search(short_query, max_results=5)

        if not results:
            return None

        # Find best match
        best_match = None
        best_score = 0.0

        saq_normalized = self._normalize(saq_name)

        for wine in results:
            vivino_normalized = self._normalize(wine.name)
            score = self._similarity(saq_normalized, vivino_normalized)

            # Bonus for high rating count
            if wine.ratings_count > 10000:
                score += 0.1
            elif wine.ratings_count > 1000:
                score += 0.05

            if score > best_score:
                best_score = score
                best_match = wine

        if best_match and best_score >= min_similarity:
            return (best_match, best_score)

        # Return best result anyway if rating exists
        if results and results[0].rating > 0:
            return (results[0], 0.3)

        return None

    def _normalize(self, text: str) -> str:
        """Normalize text for comparison."""
        text = text.lower()
        # Remove accents
        replacements = {
            'é': 'e', 'è': 'e', 'ê': 'e', 'ë': 'e',
            'à': 'a', 'â': 'a', 'ä': 'a',
            'ù': 'u', 'û': 'u', 'ü': 'u',
            'ô': 'o', 'ö': 'o',
            'î': 'i', 'ï': 'i',
            'ç': 'c', 'ñ': 'n',
        }
        for old, new in replacements.items():
            text = text.replace(old, new)
        # Remove punctuation and years
        text = re.sub(r'[^\w\s]', ' ', text)
        text = re.sub(r'\b(19|20)\d{2}\b', '', text)
        return ' '.join(text.split())

    def _similarity(self, a: str, b: str) -> float:
        """Calculate similarity."""
        from difflib import SequenceMatcher
        return SequenceMatcher(None, a, b).ratio()


async def test_vivino():
    """Test Vivino scraper."""
    test_wines = [
        "Cazal Viel Vieilles Vignes",
        "Cono Sur Bicicleta Pinot Noir",
        "M. Chapoutier Marius",
        "Kim Crawford Sauvignon Blanc",
        "Masi Campofiorin",
    ]

    print("=" * 70)
    print("VIVINO SCRAPER TEST")
    print("=" * 70)

    async with VivinoScraper(headless=True) as scraper:
        matcher = WineMatcher(scraper)

        for saq_name in test_wines:
            print(f"\nSAQ: {saq_name}")
            print("-" * 50)

            result = await matcher.find_match(saq_name)

            if result:
                wine, score = result
                print(f"  Vivino: {wine.name}")
                print(f"  Rating: {wine.rating:.1f} ({wine.ratings_count:,} reviews)")
                if wine.region or wine.country:
                    print(f"  Region: {wine.region}, {wine.country}")
                print(f"  Match Score: {score:.0%}")
                print(f"  URL: {wine.url}")
            else:
                print("  No match found")

            await asyncio.sleep(1)  # Rate limiting

    print("\n" + "=" * 70)
    print("TEST COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(test_vivino())
