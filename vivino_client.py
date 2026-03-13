#!/usr/bin/env python3
"""
Vivino API Client - Fetch wine ratings from Vivino's internal API.
"""

import re
import asyncio
import aiohttp
from dataclasses import dataclass
from typing import Optional
from difflib import SequenceMatcher


@dataclass
class VivinoWine:
    """Vivino wine data."""
    id: int
    name: str
    winery: str
    region: str
    country: str
    rating: float
    ratings_count: int
    price: Optional[float]
    vintage: Optional[int]
    grape: Optional[str]
    url: str
    thumb: Optional[str]


class VivinoClient:
    """Client for Vivino's internal API."""

    BASE_URL = "https://www.vivino.com/api/explore/explore"
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
    }

    def __init__(self, country_code: str = "CA", currency_code: str = "CAD"):
        self.country_code = country_code
        self.currency_code = currency_code
        self._session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self):
        self._session = aiohttp.ClientSession(headers=self.HEADERS)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._session:
            await self._session.close()

    async def search(
        self,
        query: str,
        min_rating: float = 1.0,
        max_results: int = 10,
    ) -> list[VivinoWine]:
        """Search for wines by name."""
        if not self._session:
            raise RuntimeError("Client not initialized. Use 'async with' context.")

        params = {
            "country_code": self.country_code,
            "currency_code": self.currency_code,
            "grape_filter": "varietal",
            "min_rating": str(min_rating),
            "order_by": "ratings_average",
            "order": "desc",
            "page": "1",
            "per_page": str(max_results),
        }

        # Add search query
        search_url = f"https://www.vivino.com/search/wines?q={query}"

        try:
            # First try the explore API with wine name filter
            async with self._session.get(
                self.BASE_URL,
                params=params,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()

        except Exception as e:
            print(f"Vivino API error: {e}")
            return []

        return self._parse_results(data)

    async def search_by_name(self, wine_name: str, max_results: int = 5) -> list[VivinoWine]:
        """Search Vivino by wine name using the search endpoint."""
        if not self._session:
            raise RuntimeError("Client not initialized. Use 'async with' context.")

        # Clean the wine name for search
        clean_name = self._clean_wine_name(wine_name)

        # Use the direct search API (more reliable)
        return await self._search_direct(clean_name, max_results)

    async def _search_direct(self, query: str, max_results: int) -> list[VivinoWine]:
        """Direct search using Vivino's search suggest API."""
        if not self._session:
            return []

        # Vivino's autocomplete/suggest API
        url = "https://www.vivino.com/search/suggestions"
        params = {"q": query}

        try:
            async with self._session.get(
                url,
                params=params,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()

            wines = []
            # Parse wine suggestions
            for item in data.get("wines", [])[:max_results]:
                try:
                    wine = VivinoWine(
                        id=item.get("id", 0),
                        name=item.get("name", ""),
                        winery=item.get("winery", {}).get("name", "") if item.get("winery") else "",
                        region=item.get("region", {}).get("name", "") if item.get("region") else "",
                        country=item.get("region", {}).get("country", {}).get("name", "") if item.get("region") else "",
                        rating=item.get("statistics", {}).get("ratings_average", 0) if item.get("statistics") else 0,
                        ratings_count=item.get("statistics", {}).get("ratings_count", 0) if item.get("statistics") else 0,
                        price=None,
                        vintage=None,
                        grape=None,
                        url=f"https://www.vivino.com/wines/{item.get('id', '')}",
                        thumb=item.get("image", {}).get("location") if item.get("image") else None,
                    )
                    if wine.rating > 0:
                        wines.append(wine)
                except Exception:
                    continue

            return wines

        except Exception as e:
            print(f"Direct search error: {e}")
            return []

    async def _search_alternative(self, query: str, max_results: int) -> list[VivinoWine]:
        """Alternative search using web search endpoint."""
        if not self._session:
            return []

        # Try the wines search API
        url = f"https://www.vivino.com/api/wines/search"
        params = {
            "q": query,
            "per_page": str(max_results),
        }

        try:
            async with self._session.get(
                url,
                params=params,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()

            wines = []
            for item in data.get("wines", []):
                try:
                    wine = VivinoWine(
                        id=item.get("id", 0),
                        name=item.get("name", ""),
                        winery=item.get("winery", {}).get("name", ""),
                        region=item.get("region", {}).get("name", "") if item.get("region") else "",
                        country=item.get("region", {}).get("country", {}).get("name", "") if item.get("region") else "",
                        rating=item.get("statistics", {}).get("ratings_average", 0),
                        ratings_count=item.get("statistics", {}).get("ratings_count", 0),
                        price=None,
                        vintage=None,
                        grape=item.get("style", {}).get("varietal_name") if item.get("style") else None,
                        url=f"https://www.vivino.com/wines/{item.get('id', '')}",
                        thumb=item.get("image", {}).get("location") if item.get("image") else None,
                    )
                    wines.append(wine)
                except Exception:
                    continue

            return wines

        except Exception as e:
            print(f"Alternative search error: {e}")
            return []

    def _parse_results(self, data: dict) -> list[VivinoWine]:
        """Parse API response into VivinoWine objects."""
        wines = []
        matches = data.get("explore_vintage", {}).get("matches", [])

        for match in matches:
            try:
                vintage_data = match.get("vintage", {})
                wine_data = vintage_data.get("wine", {})
                stats = vintage_data.get("statistics", {})
                price_data = match.get("price", {})

                wine = VivinoWine(
                    id=wine_data.get("id", 0),
                    name=wine_data.get("name", ""),
                    winery=wine_data.get("winery", {}).get("name", ""),
                    region=wine_data.get("region", {}).get("name", "") if wine_data.get("region") else "",
                    country=wine_data.get("region", {}).get("country", {}).get("name", "") if wine_data.get("region") else "",
                    rating=stats.get("ratings_average", 0),
                    ratings_count=stats.get("ratings_count", 0),
                    price=price_data.get("amount") if price_data else None,
                    vintage=vintage_data.get("year"),
                    grape=wine_data.get("style", {}).get("varietal_name") if wine_data.get("style") else None,
                    url=f"https://www.vivino.com/wines/{wine_data.get('id', '')}",
                    thumb=vintage_data.get("image", {}).get("location") if vintage_data.get("image") else None,
                )
                wines.append(wine)
            except Exception as e:
                continue

        return wines

    def _clean_wine_name(self, name: str) -> str:
        """Clean wine name for better search results."""
        # Remove vintage year
        name = re.sub(r'\b(19|20)\d{2}\b', '', name)
        # Remove common suffixes
        name = re.sub(r'\b(reserve|reserva|gran|grand|cru|cuvee|selection)\b', '', name, flags=re.IGNORECASE)
        # Remove extra whitespace
        name = ' '.join(name.split())
        return name.strip()


class WineMatcher:
    """Match SAQ wines to Vivino wines using fuzzy matching."""

    def __init__(self, vivino_client: VivinoClient):
        self.client = vivino_client

    async def find_match(
        self,
        saq_name: str,
        saq_price: Optional[float] = None,
        min_similarity: float = 0.4,
    ) -> Optional[tuple[VivinoWine, float]]:
        """
        Find best Vivino match for a SAQ wine.
        Returns (wine, similarity_score) or None.
        """
        # Search Vivino
        results = await self.client.search_by_name(saq_name, max_results=10)

        if not results:
            # Try with just the first few words (producer name)
            words = saq_name.split()[:3]
            short_query = ' '.join(words)
            results = await self.client.search_by_name(short_query, max_results=10)

        if not results:
            return None

        # Find best match using fuzzy matching
        best_match = None
        best_score = 0.0

        saq_normalized = self._normalize(saq_name)

        for wine in results:
            # Compare with full name (winery + wine name)
            vivino_full = f"{wine.winery} {wine.name}"
            vivino_normalized = self._normalize(vivino_full)

            # Calculate similarity
            score = self._similarity(saq_normalized, vivino_normalized)

            # Bonus for matching winery name
            saq_words = set(saq_normalized.lower().split())
            winery_words = set(self._normalize(wine.winery).lower().split())
            if saq_words & winery_words:
                score += 0.1

            # Bonus for high rating count (more reliable)
            if wine.ratings_count > 10000:
                score += 0.05
            elif wine.ratings_count > 1000:
                score += 0.02

            if score > best_score:
                best_score = score
                best_match = wine

        if best_match and best_score >= min_similarity:
            return (best_match, best_score)

        return None

    def _normalize(self, text: str) -> str:
        """Normalize text for comparison."""
        # Lowercase
        text = text.lower()
        # Remove accents (simple version)
        replacements = {
            'é': 'e', 'è': 'e', 'ê': 'e', 'ë': 'e',
            'à': 'a', 'â': 'a', 'ä': 'a',
            'ù': 'u', 'û': 'u', 'ü': 'u',
            'ô': 'o', 'ö': 'o',
            'î': 'i', 'ï': 'i',
            'ç': 'c',
            'ñ': 'n',
        }
        for old, new in replacements.items():
            text = text.replace(old, new)
        # Remove punctuation
        text = re.sub(r'[^\w\s]', ' ', text)
        # Remove vintage years
        text = re.sub(r'\b(19|20)\d{2}\b', '', text)
        # Remove extra whitespace
        text = ' '.join(text.split())
        return text

    def _similarity(self, a: str, b: str) -> float:
        """Calculate similarity between two strings."""
        return SequenceMatcher(None, a, b).ratio()


async def test_vivino():
    """Test Vivino client with sample SAQ wines."""
    test_wines = [
        "Cazal Viel Vieilles Vignes",
        "Cono Sur Bicicleta Pinot Noir",
        "M. Chapoutier Marius",
        "Kim Crawford Sauvignon Blanc",
        "Masi Campofiorin",
    ]

    print("=" * 70)
    print("VIVINO INTEGRATION TEST")
    print("=" * 70)

    async with VivinoClient(country_code="CA", currency_code="CAD") as client:
        matcher = WineMatcher(client)

        for saq_name in test_wines:
            print(f"\nSAQ: {saq_name}")
            print("-" * 50)

            result = await matcher.find_match(saq_name)

            if result:
                wine, score = result
                print(f"  Vivino Match: {wine.winery} {wine.name}")
                print(f"  Rating: {wine.rating:.1f} ({wine.ratings_count:,} reviews)")
                print(f"  Region: {wine.region}, {wine.country}")
                print(f"  Match Score: {score:.0%}")
                print(f"  URL: {wine.url}")
            else:
                print("  No match found")

            await asyncio.sleep(0.5)  # Rate limiting

    print("\n" + "=" * 70)
    print("TEST COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(test_vivino())
