import json
from .utils import generate_id
import re
from typing import Dict, Any, List, Optional
from bs4 import BeautifulSoup

# Patterns for boilerplate text to filter out of transcripts
BOILERPLATE_PATTERNS = [
    re.compile(r"below is an approximation of this video", re.IGNORECASE),
    re.compile(r"to see any graphs, charts, graphics, images", re.IGNORECASE),
    re.compile(r"please consider.*volunteer", re.IGNORECASE),
]

def clean_transcript(paragraphs: List[str]) -> str:
    """
    Cleans a list of transcript paragraphs by filtering out boilerplate elements
    and joining them with double newlines.
    """
    cleaned = []
    for p in paragraphs:
        text = p.strip()
        if not text:
            continue
        
        # Check if paragraph matches any boilerplate pattern
        is_boilerplate = False
        for pattern in BOILERPLATE_PATTERNS:
            if pattern.search(text):
                is_boilerplate = True
                break
        
        if not is_boilerplate:
            cleaned.append(text)
            
    return "\n\n".join(cleaned)

# parse_video_page implementation moved below

# --- Helper functions for parse_video_page -----------------------------------

def _init_data(url: str) -> Dict[str, Any]:
    """Create the result dictionary with default values and a generated ID."""
    return {
        "id": generate_id(url),
        "url": url,
        "title": "",
        "description": "",
        "upload_date": None,
        "duration": None,
        "thumbnail_url": None,
        "video_url": None,
        "embed_url": None,
        "transcript_raw": [],
        "transcript_clean": "",
    }

def _extract_fallbacks(soup: BeautifulSoup, data: Dict[str, Any]) -> None:
    """Populate title, description, and thumbnail from common meta tags."""
    title_tag = soup.find("title")
    if title_tag:
        data["title"] = title_tag.get_text().strip()
    meta_desc = soup.find("meta", attrs={"name": "description"}) or soup.find(
        "meta", attrs={"property": "og:description"}
    )
    if meta_desc:
        data["description"] = meta_desc.get("content", "").strip()
    og_image = soup.find("meta", attrs={"property": "og:image"})
    if og_image:
        data["thumbnail_url"] = og_image.get("content", "").strip()

def _parse_json_ld(soup: BeautifulSoup, data: Dict[str, Any]) -> None:
    """Extract structured video metadata from JSON‑LD script tags."""
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            js_data = json.loads(script.string or "")
            # Yoast schema may use @graph; handle both list and dict forms
            graph = js_data.get("@graph", [js_data]) if isinstance(js_data, dict) else []
            if isinstance(js_data, list):
                graph = js_data
            for item in graph:
                if not isinstance(item, dict):
                    continue
                if item.get("@type") == "VideoObject":
                    data["title"] = item.get("name", data["title"])
                    data["description"] = item.get("description", data["description"])
                    data["upload_date"] = item.get("uploadDate", data["upload_date"])
                    data["duration"] = item.get("duration", data["duration"])
                    data["thumbnail_url"] = item.get("thumbnailUrl", data["thumbnail_url"])
                    data["video_url"] = item.get("contentUrl", data["video_url"])
                    data["embed_url"] = item.get("embedUrl", data["embed_url"])
                    return  # stop after first VideoObject
        except (json.JSONDecodeError, TypeError, AttributeError):
            continue

def _extract_transcript(soup: BeautifulSoup, data: Dict[str, Any]) -> None:
    """Find the transcript container and populate raw and cleaned transcript fields."""
    container = soup.find(id="transcript") or soup.find(id="collapseTranscript")
    if not container:
        return
    paragraphs = []
    for p in container.find_all("p"):
        text = p.get_text().strip()
        if text:
            paragraphs.append(text)
    data["transcript_raw"] = paragraphs
    data["transcript_clean"] = clean_transcript(paragraphs)

def parse_video_page(html_content: str, url: str) -> Dict[str, Any]:
    """Parse a NutritionFacts.org video page and return all extracted metadata."""
    soup = BeautifulSoup(html_content, "lxml")
    data = _init_data(url)
    _extract_transcript(soup, data)
    _extract_audio(soup, data)
    return data
