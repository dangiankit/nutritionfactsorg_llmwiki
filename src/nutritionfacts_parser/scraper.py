import time
import json
from .utils import generate_id
import logging
import re
from collections import Counter
import requests
import concurrent.futures
from typing import List, Optional, Dict
from bs4 import BeautifulSoup
from urllib.parse import urlparse
from requests.adapters import HTTPAdapter
from urllib3.util import Retry
import re
logger = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": "NutritionFactsParser/0.1.0 (+https://github.com/nutritionfactsorg_llmwiki; contact: scraper@nutritionfactsorg_llmwiki.org)"
}

class NutritionFactsScraper:
    """Fetch and parse NutritionFacts.org sitemaps and video pages.

    The class is deliberately lightweight – it only handles network I/O, XML parsing,
    and simple post‑processing such as de‑duplication and unwanted‑URL filtering.
    """

    def __init__(self, delay_seconds: float = 1.0, pool_size: int = 25, headers: Optional[dict] = None):
        self.delay_seconds = delay_seconds
        self.headers = headers or DEFAULT_HEADERS
        self.session = requests.Session()
        self.session.headers.update(self.headers)
        # Configure connection pool and retry policy
        retry = Retry(
            total=5,
            backoff_factor=0.5,
            status_forcelist=[500, 502, 504],
            allowed_methods=["GET"],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(pool_connections=pool_size, pool_maxsize=pool_size, max_retries=retry)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    @staticmethod
    def _get_text(element) -> str:
        """Safely return stripped text from a BeautifulSoup element or empty string."""
        return element.get_text().strip() if element else ""

    # ---------------------------------------------------------------------
    # Low‑level HTTP helper
    # ---------------------------------------------------------------------
    def _get(self, url: str, skip_delay: bool = False) -> requests.Response:
        """Perform a GET request respecting the politeness delay.

        ``skip_delay`` is used when we fetch many sitemap files in rapid succession –
        we only want the delay between actual video page fetches.
        """
        logger.debug(f"Fetching: {url}")
        response = self.session.get(url, timeout=30)
        response.raise_for_status()
        if not skip_delay and self.delay_seconds > 0:
            time.sleep(self.delay_seconds)
        return response

    # ---------------------------------------------------------------------
    # Sitemap discovery
    # ---------------------------------------------------------------------
    def get_sitemap_urls(self, index_url: str = "https://nutritionfacts.org/sitemap.xml") -> List[str]:
        """Return a sorted list of all video‑related sitemap URLs.

        Only URLs that contain the substring ``video-sitemap`` are considered.
        """
        try:
            response = self._get(index_url, skip_delay=True)
        except Exception as e:
            logger.error(f"Failed to fetch sitemap index from {index_url}: {e}")
            return []

        soup = BeautifulSoup(response.content, "xml")
        sitemaps: List[str] = []
        for loc in soup.find_all("loc"):
            sitemap_url = self._get_text(loc)
            if "video-sitemap" in sitemap_url.lower() or "audio-sitemap" in sitemap_url.lower():
                sitemaps.append(sitemap_url)
        logger.info(f"Found {len(sitemaps)} video/audio sitemaps in sitemap index.")
        return sorted(sitemaps)

    # ---------------------------------------------------------------------
    # Sitemap parsing helpers
    # ---------------------------------------------------------------------
    def _parse_sitemap(self, soup: BeautifulSoup) -> List[Dict[str, str]]:
        """Parse a sitemap ``soup`` and return a list of filtered entries.

        Each entry is a ``dict`` with at least a ``url`` key and optional metadata
        fields (``lastmod``, ``changefreq``, ``priority``). Example::

            {
                "url": "https://nutritionfacts.org/video/healthy-eating",
                "lastmod": "2024-08-01",
                "changefreq": "weekly",
                "priority": "0.8",
                "id": "video_healthy-eating"
            }
        """
        entries: List[Dict[str, str]] = []
        for url_el in soup.find_all("url"):
            loc = self._extract_loc(url_el)

            # Missing or malformed `<loc>` element
            if not loc:
                continue  # malformed entry

            # URL that we deliberately want to ignore
            if self._is_unwanted(loc):
                continue

            entry: Dict[str, str] = {"url": loc}
            entry.update(self._extract_optional_fields(url_el))
            entries.append(entry)
        return entries

    # ---------------------------------------------------------------------
    # Helper methods for sitemap parsing
    # ---------------------------------------------------------------------
    def _extract_loc(self, url_el: BeautifulSoup) -> Optional[str]:
        """Extract and clean the <loc> URL from a sitemap entry.

        Returns the stripped URL string or ``None`` if missing.
        """
        loc = url_el.find("loc")
        if loc and self._get_text(loc):
            return self._get_text(loc)
        return None

    def _is_unwanted(self, url: str) -> bool:
        """Check if the URL should be ignored based on unwanted terms.

        Unwanted terms: "post_type", "webinar", "videos".
        """
        return any(term in url for term in ["post_type", "webinar", "videos"])

    def _extract_optional_fields(self, url_el: BeautifulSoup) -> Dict[str, str]:
        """Extract optional metadata fields (lastmod, changefreq, priority).

        Returns a dict with any found fields.
        """
        data: Dict[str, str] = {}
        for tag in ("lastmod", "changefreq", "priority"):
            el = url_el.find(tag)
            if el and self._get_text(el):
                data[tag] = self._get_text(el)
        return data

    def _determine_content_type(self, sitemap_url: str) -> str:
        """Return the base content type for a sitemap URL.

        Handles filenames like "video-sitemap33.xml" by stripping digits and the
        ".xml" extension, then checking for "audio-sitemap" or "video-sitemap".
        """
        path = urlparse(sitemap_url).path
        filename = path.split('/')[-1]
        
        # Remove digits and .xml extension (case‑insensitive)
        base_name = re.sub(r"\d+|\.xml$", "", filename, flags=re.IGNORECASE)

        # Strip trailing "-sitemap" if present
        if base_name.lower().endswith('-sitemap'):
            base_name = base_name[:-len('-sitemap')]
        base_name = base_name.strip()
        low = base_name.lower()
        if low:
            return low
        return "other"

    def extract_urls_from_sitemap(self, sitemap_url: str, skip_delay: bool = True) -> List[Dict[str, str]]:
        """Fetch a sitemap XML file and return the parsed entries.

        The heavy lifting is delegated to :meth:`_parse_sitemap`. This method also
        adds a ``content_type`` field to each entry based on the originating
        sitemap URL (audio vs video) and simple URL heuristics (e.g. ``hnta``).
        """
        try:
            response = self._get(sitemap_url, skip_delay=skip_delay)
        except Exception as e:
            logger.error(f"Failed to fetch sitemap {sitemap_url}: {e}")
            return []
        soup = BeautifulSoup(response.content, "xml")
        entries = self._parse_sitemap(soup)
        base_type = self._determine_content_type(sitemap_url)

        # Annotate each entry with the derived content type
        for entry in entries:
            entry["content_type"] = base_type
        logger.debug(f"Extracted {len(entries)} URL entries from {sitemap_url}")
        return entries

    # ---------------------------------------------------------------------
    # De‑duplication
    # ---------------------------------------------------------------------
    def _deduplicate_metadata(self, entries: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """Deduplicate entries by URL, preferring the most recent ``lastmod`` value.
        """
        deduped: Dict[str, Dict[str, str]] = {}
        for entry in entries:
            url = entry.get("url")
            if not url:
                continue
            existing = deduped.get(url)
            if not existing:
                deduped[url] = entry
                continue
            has_new = "lastmod" in entry and entry["lastmod"]
            has_old = "lastmod" in existing and existing["lastmod"]
            if has_new and not has_old:
                deduped[url] = entry
            elif has_new and has_old:
                try:
                    from datetime import datetime
                    fmt = "%Y-%m-%d"
                    new_date = datetime.strptime(entry["lastmod"], fmt)
                    old_date = datetime.strptime(existing["lastmod"], fmt)
                    if new_date > old_date:
                        deduped[url] = entry
                except Exception:
                    pass
        return list(deduped.values())

    def _assign_ids(self, entries: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """Assign a human‑readable identifier to each entry.

        The ID is derived from the URL path (e.g. ``/video/healthy‑eating`` becomes
        ``video_healthy-eating``). If duplicate IDs occur, a numeric suffix is added
        to ensure uniqueness.
        """
        seen: Dict[str, int] = {}
        for entry in entries:
            url = entry.get("url", "")
            if not url:
                continue
            # Generate base ID using shared utility
            base_id = generate_id(url)
            # Ensure uniqueness
            count = seen.get(base_id, 0)
            unique_id = f"{base_id}_{count}" if count else base_id
            seen[base_id] = count + 1
            entry.setdefault("id", unique_id)
        return entries

    # ---------------------------------------------------------------------
    # Public API – metadata collection
    # ---------------------------------------------------------------------
    def get_all_video_page_metadata(self, index_url: str = "https://nutritionfacts.org/sitemap.xml", max_workers: int = 15) -> List[Dict[str, str]]:
        """Collect metadata for every video page across all sitemaps.

        The method runs the network‑bound ``extract_urls_from_sitemap`` calls in a
        ``ThreadPoolExecutor`` to maximise throughput while staying memory‑friendly.
        """
        sitemaps = self.get_sitemap_urls(index_url)
        logger.info(
            f"Extracting video metadata from {len(sitemaps)} sitemaps concurrently (max workers: {max_workers})..."
        )
        all_entries: List[Dict[str, str]] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_sitemap = {executor.submit(self.extract_urls_from_sitemap, sm, True): sm for sm in sitemaps}
            for future in concurrent.futures.as_completed(future_to_sitemap):
                try:
                    entries = future.result()
                    all_entries.extend(entries)
                except Exception as e:
                    logger.error(f"Error processing sitemap for metadata: {e}")
        # De‑duplicate after we have gathered everything; filtering is already done in parsing
        all_entries = self._deduplicate_metadata(all_entries)
        # Assign a unique identifier to each entry
        all_entries = self._assign_ids(all_entries)

        # Summarize counts per content type
        type_counts = Counter(entry.get("content_type", "other") for entry in all_entries)
        # Convert Counter to a plain dict for easier inspection
        total_counts = dict(type_counts)
        total = sum(total_counts.values())
        # Build a readable breakdown string from the dict
        breakdown = ", ".join(f"{k}: {v}" for k, v in total_counts.items())
        logger.info(
            f"Total metadata entries retrieved: {total} ({breakdown})"
        )
        return all_entries

    # ---------------------------------------------------------------------
    # Convenience helpers used by the CLI
    # ---------------------------------------------------------------------
    def filter_video_urls_by_path(self, urls: List[str], path_prefix: str = "/video/") -> List[str]:
        """Return only URLs that start with the given ``path_prefix``.
        """
        return [url for url in urls if urlparse(url).path.startswith(path_prefix) and urlparse(url).path != path_prefix]

    def fetch_video_page_html(self, video_url: str) -> Optional[str]:
        """Download the raw HTML of a video page.
        """
        try:
            response = self._get(video_url, skip_delay=False)
            return response.text
        except Exception as e:
            logger.error(f"Failed to fetch video page {video_url}: {e}")
            return None
