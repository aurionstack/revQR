import logging
import re
import urllib.request
import asyncio
from typing import Optional
from urllib.parse import unquote

logger = logging.getLogger(__name__)


async def resolve_google_place_id(input_text: str) -> dict:
    """
    Resolve and convert a Google Business Profile URL or Google Maps link
    into a valid Google Place ID (ChIJ...).
    """
    text = input_text.strip()
    if not text:
        return {"success": False, "error": "Please enter a Google Maps or Business Profile URL."}

    # 1. Direct Place ID input (e.g. ChIJN1t_tDeuEmsRUsoyG83frY4)
    if re.match(r"^ChIJ[a-zA-Z0-9_-]{20,}$", text):
        return {"success": True, "place_id": text, "message": "Valid Google Place ID detected."}

    # 2. Check query params in the raw string (e.g. placeid=... or query_place_id=...)
    m = re.search(r"[?&](?:placeid|place_id|query_place_id)=(ChIJ[a-zA-Z0-9_-]{20,})", text)
    if m:
        return {"success": True, "place_id": m.group(1), "message": "Place ID extracted from URL parameters."}

    # 3. Direct ChIJ in URL string
    m = re.search(r"ChIJ[a-zA-Z0-9_-]{20,}", text)
    if m:
        return {"success": True, "place_id": m.group(0), "message": "Place ID extracted directly."}

    # 4. If URL, follow redirects in background thread and parse response
    target_url = text
    if not target_url.startswith("http://") and not target_url.startswith("https://"):
        target_url = "https://" + target_url

    def _fetch_and_extract():
        try:
            req = urllib.request.Request(
                target_url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                    "Accept-Language": "en-US,en;q=0.9",
                }
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                final_url = response.geturl()
                content = response.read(300000).decode("utf-8", errors="ignore")

                # Check final URL for placeid query param or ChIJ
                m_url = re.search(r"[?&](?:placeid|place_id|query_place_id)=(ChIJ[a-zA-Z0-9_-]{20,})", final_url)
                if m_url:
                    return {"success": True, "place_id": m_url.group(1), "resolved_url": final_url, "message": "Place ID resolved successfully."}

                m_chij = re.search(r"ChIJ[a-zA-Z0-9_-]{20,}", final_url)
                if m_chij:
                    return {"success": True, "place_id": m_chij.group(0), "resolved_url": final_url, "message": "Place ID found in redirected URL."}

                # Check content for ChIJ Place IDs
                matches = re.findall(r"ChIJ[a-zA-Z0-9_-]{20,}", content)
                if matches:
                    return {"success": True, "place_id": matches[0], "resolved_url": final_url, "message": "Place ID extracted from Google Maps page."}

                # Check for business name in URL path
                name_match = re.search(r"/place/([^/@]+)", final_url)
                biz_name = unquote(name_match.group(1).replace("+", " ")) if name_match else None

                # Check for hex FTID in url: e.g. 0x390cfd5b347eb62d:0x...
                m_hex = re.search(r"!1s(0x[0-9a-fA-F]+:0x[0-9a-fA-F]+)", final_url)
                if m_hex:
                    return {"success": True, "place_id": m_hex.group(1), "name": biz_name, "resolved_url": final_url, "message": "Location FTID identifier extracted."}

                return {"success": False, "error": "Could not locate Place ID from this URL. Please verify the link or enter the Place ID manually."}
        except Exception as e:
            return {"success": False, "error": f"Failed to connect to URL: {str(e)}"}

    return await asyncio.to_thread(_fetch_and_extract)


async def fetch_google_reviews(place_id: str) -> Optional[str]:
    """
    Fetch Google Reviews for a given place_id.
    Currently uses mock data for demonstration.
    In production, this would call Google Places API or SerpApi.
    """
    if not place_id:
        return None
        
    logger.info(f"Fetching reviews for place_id: {place_id}")
    
    # Mock response
    mock_reviews = [
        "The food was absolutely incredible, especially the spicy wings. The bartender Sarah was very friendly and made great drinks.",
        "A wonderful family-owned Italian place. The garlic bread is a must-try!",
        "Great atmosphere and quick service. I've been coming here since 1990 and it never disappoints.",
        "Sarah always remembers my order. Best spot in town for a quick lunch.",
        "Can get a bit crowded on weekends, but the spicy wings make it worth the wait."
    ]
    
    # Summarize into a single context string
    context = "Summary of recent Google Reviews:\n"
    for i, review in enumerate(mock_reviews, 1):
        context += f"- {review}\n"
        
    return context
