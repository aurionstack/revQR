import json
import logging
from google import genai
from google.genai import types
from app.config import settings

logger = logging.getLogger(__name__)

# Initialize the Gemini client if API key is present
client = None
if settings.GEMINI_API_KEY:
    try:
        client = genai.Client(api_key=settings.GEMINI_API_KEY)
    except Exception as e:
        logger.error(f"Failed to initialize Gemini client: {e}")


def _get_fallback_variations(rating: int, business_name: str, notes: str = "") -> dict:
    """Provides instant reliable fallback variations if AI service is unreachable."""
    if rating == 5:
        return {
            "punchy": f"Loved my visit to {business_name}! Outstanding service and great experience.",
            "detailed": f"Had a fantastic experience at {business_name}. The team was attentive, welcoming, and everything was handled with care. Highly recommend to everyone!",
            "warm": f"Cannot say enough good things about {business_name}! Absolutely loved it and will definitely be coming back regularly with friends."
        }
    elif rating == 4:
        return {
            "punchy": f"Really good experience at {business_name}. Friendly staff and clean space.",
            "detailed": f"Visited {business_name} today. Overall very satisfied with the service and quality. Just a couple minor things, but definitely coming back.",
            "warm": f"Such a lovely spot! {business_name} delivered a great experience. Friendly folks and good vibes."
        }
    elif rating == 3:
        return {
            "punchy": f"Decent experience at {business_name}, but there is room for improvement.",
            "detailed": f"My visit to {business_name} was okay. Some parts were fine, but service could be faster and more attentive.",
            "warm": f"Good potential at {business_name}. Service was polite, though a few things felt a bit inconsistent during my visit."
        }
    elif rating == 2:
        return {
            "punchy": f"Disappointed with my visit to {business_name}. Expected much better.",
            "detailed": f"Unfortunately, our experience at {business_name} fell short. The service was slow and didn't meet expectations.",
            "warm": f"Had higher hopes for {business_name}, but the experience wasn't pleasant today. Hope management looks into this."
        }
    else:
        return {
            "punchy": f"Very poor experience at {business_name}. Would not recommend.",
            "detailed": f"Very unhappy with my visit to {business_name}. The service and quality were completely unacceptable.",
            "warm": f"Disappointed with {business_name}. We faced multiple issues and received very little help from staff."
        }


async def generate_review_variations(
    rating: int,
    notes: str,
    business_name: str,
    custom_prompt: str = None,
    scraped_context: str = None
) -> dict:
    """
    Generate 3 distinct review variations:
    1. punchy: Short & crisp (1-2 sentences)
    2. detailed: Specific & structured (2-3 sentences)
    3. warm: Enthusiastic & personal (2-3 sentences)
    """
    fallback = _get_fallback_variations(rating, business_name, notes)

    if not client:
        return fallback

    prompt_parts = [
        f"You are helping a customer write a Google Review for '{business_name}'.",
        f"Star Rating: {rating} out of 5 stars.",
        f"Customer Notes/Keywords: '{notes if notes.strip() else 'Great experience'}'\n",
    ]

    if scraped_context:
        prompt_parts.append(
            f"Context on what previous Google reviewers love about this business:\n{scraped_context}\n"
            "Subtly draw authentic phrasing from this context if it fits naturally."
        )

    if custom_prompt:
        prompt_parts.append(
            f"Business owner special guidelines:\n'{custom_prompt}'\n"
        )

    prompt_parts.append(
        "Generate THREE distinct review variations written in the first person ('I'/'We'):\n"
        "1. 'punchy': Short, crisp, and direct (1-2 sentences max).\n"
        "2. 'detailed': Thoughtful, mentions specific details/service (2-3 sentences).\n"
        "3. 'warm': Enthusiastic, friendly, high praise or constructive recommendation (2-3 sentences).\n\n"
        "Return ONLY a valid JSON object with the keys 'punchy', 'detailed', and 'warm'. Do not wrap in markdown quotes if possible."
    )

    prompt = "\n".join(prompt_parts)

    try:
        response = await client.aio.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.75,
                response_mime_type="application/json",
            )
        )
        text = response.text.strip()
        data = json.loads(text)
        if isinstance(data, dict) and "punchy" in data and "detailed" in data and "warm" in data:
            return data
        elif isinstance(data, dict) and len(data) > 0:
            # fill missing keys
            return {
                "punchy": data.get("punchy") or fallback["punchy"],
                "detailed": data.get("detailed") or fallback["detailed"],
                "warm": data.get("warm") or fallback["warm"],
            }
        return fallback
    except Exception as e:
        logger.error(f"Gemini API error during review variations generation: {e}")
        return fallback


async def generate_review(
    rating: int,
    notes: str,
    business_name: str,
    custom_prompt: str = None,
    scraped_context: str = None
) -> str:
    """Backward compatibility helper returning the primary (detailed or warm) review text."""
    variations = await generate_review_variations(
        rating=rating,
        notes=notes,
        business_name=business_name,
        custom_prompt=custom_prompt,
        scraped_context=scraped_context
    )
    return variations.get("detailed") or variations.get("punchy") or "Great experience!"


async def generate_review_reply(
    rating: int,
    review_text: str,
    business_name: str,
    owner_notes: str = None
) -> dict:
    """
    Generate professional, authentic owner replies for a customer review.
    Returns:
    {
        "warm": "...",
        "short": "...",
        "deescalate": "..." (if rating <= 3) or "seo_rich": "..." (if rating >= 4)
    }
    """
    if not client:
        if rating >= 4:
            return {
                "warm": f"Thank you so much for your kind words! We're thrilled you had a great experience at {business_name} and look forward to welcoming you back soon!",
                "short": f"Thanks for the 5-star review! We appreciate your support for {business_name}.",
                "seo_rich": f"Thank you for choosing {business_name}! Our team takes immense pride in providing the best quality and service in town. See you again soon!"
            }
        else:
            return {
                "warm": f"Thank you for sharing your feedback. We sincerely apologize that your visit didn't meet our usual high standards. We'd love the opportunity to make this right.",
                "short": f"We're sorry to hear about your experience. Please reach out to our management directly so we can resolve this.",
                "deescalate": f"We appreciate your honest feedback. Providing a great experience at {business_name} is our top priority. Please call or message us so we can personally address your concerns."
            }

    prompt_parts = [
        f"You are the owner of '{business_name}'. Write Google Business profile replies to a customer review.",
        f"Customer Rating: {rating} / 5 stars.",
        f"Customer Review: \"{review_text}\"\n"
    ]

    if owner_notes:
        prompt_parts.append(f"Owner's context/instructions: {owner_notes}\n")

    if rating >= 4:
        prompt_parts.append(
            "Generate 3 response options:\n"
            "1. 'warm': Gracious, warm gratitude (2-3 sentences).\n"
            "2. 'short': Quick, polite acknowledgment (1 sentence).\n"
            "3. 'seo_rich': Mentions the business name and gratitude with natural local SEO phrasing.\n"
        )
    else:
        prompt_parts.append(
            "Generate 3 response options for a critical review:\n"
            "1. 'warm': Empathetic apology and commitment to quality.\n"
            "2. 'short': Brief, polite acknowledgment and invitation to contact directly.\n"
            "3. 'deescalate': Professional, takes the conversation offline politely (e.g., 'Please email/call us directly so we can make this right').\n"
        )

    prompt_parts.append("Return ONLY a valid JSON object with keys: 'warm', 'short', and 'option3'.")

    try:
        response = await client.aio.models.generate_content(
            model='gemini-2.5-flash',
            contents="\n".join(prompt_parts),
            config=types.GenerateContentConfig(
                temperature=0.7,
                response_mime_type="application/json",
            )
        )
        data = json.loads(response.text.strip())
        return {
            "warm": data.get("warm", ""),
            "short": data.get("short", ""),
            "seo_rich" if rating >= 4 else "deescalate": data.get("option3", data.get("seo_rich", data.get("deescalate", "")))
        }
    except Exception as e:
        logger.error(f"Error generating review reply: {e}")
        return {
            "warm": f"Thank you for your feedback! We truly appreciate you visiting {business_name}.",
            "short": "Thank you for reviewing us!",
            "seo_rich" if rating >= 4 else "deescalate": f"Thank you from all of us at {business_name}!"
        }

