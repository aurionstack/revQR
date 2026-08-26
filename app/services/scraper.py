import logging
from typing import Optional

logger = logging.getLogger(__name__)

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
