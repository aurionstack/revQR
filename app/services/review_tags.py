"""Business-aware highlight suggestions for the public review flow."""

from __future__ import annotations

import json


GENERIC_TAGS = {
    "high": ["Great service", "Friendly team", "Quality", "Quick experience", "Good value"],
    "mid": ["Service", "Staff", "Quality", "Wait time", "Value"],
    "low": ["Wait time", "Communication", "Quality", "Cleanliness", "Pricing"],
}


INDUSTRIES = (
    (
        ("restaurant", "cafe", "coffee", "bakery", "food", "dining", "hotel kitchen", "bar", "bistro"),
        {
            "high": ["Food quality", "Taste", "Friendly staff", "Quick service", "Atmosphere"],
            "mid": ["Food quality", "Service", "Atmosphere", "Wait time", "Value"],
            "low": ["Food quality", "Wait time", "Order accuracy", "Cleanliness", "Pricing"],
        },
    ),
    (
        ("salon", "spa", "beauty", "hair", "makeup", "nail", "grooming", "barber"),
        {
            "high": ["Styling result", "Skilled professional", "Friendly service", "Clean salon", "Relaxing experience"],
            "mid": ["Service result", "Staff", "Cleanliness", "Appointment time", "Value"],
            "low": ["Service result", "Appointment delay", "Staff communication", "Cleanliness", "Pricing"],
        },
    ),
    (
        ("clinic", "doctor", "dental", "dentist", "hospital", "medical", "healthcare", "physio", "therapy"),
        {
            "high": ["Doctor's care", "Clear explanation", "Helpful staff", "Short wait", "Clean clinic"],
            "mid": ["Consultation", "Explanation", "Staff", "Wait time", "Cleanliness"],
            "low": ["Wait time", "Communication", "Staff support", "Cleanliness", "Billing"],
        },
    ),
    (
        ("website", "web design", "software", "seo", "digital marketing", "app development", "technology", "agency"),
        {
            "high": ["Design quality", "Technical expertise", "Clear communication", "On-time delivery", "SEO support"],
            "mid": ["Design quality", "Communication", "Delivery time", "Technical support", "Value"],
            "low": ["Communication", "Delivery delays", "Design revisions", "Technical support", "Pricing"],
        },
    ),
    (
        ("print", "printing", "sublimation", "signage", "custom merchandise", "personalised gift", "packaging"),
        {
            "high": ["Print quality", "Colour accuracy", "Product finish", "Quick turnaround", "Helpful service"],
            "mid": ["Print quality", "Product finish", "Turnaround time", "Communication", "Value"],
            "low": ["Print quality", "Colour accuracy", "Delivery delay", "Order accuracy", "Pricing"],
        },
    ),
    (
        ("hotel", "resort", "guest house", "homestay", "accommodation", "rooms"),
        {
            "high": ["Comfortable room", "Helpful staff", "Cleanliness", "Location", "Smooth check-in"],
            "mid": ["Room comfort", "Staff", "Cleanliness", "Location", "Value"],
            "low": ["Room condition", "Cleanliness", "Staff response", "Check-in", "Pricing"],
        },
    ),
    (
        ("gym", "fitness", "yoga", "workout", "personal trainer", "sports academy"),
        {
            "high": ["Trainer support", "Equipment", "Clean facility", "Workout environment", "Good value"],
            "mid": ["Trainer support", "Equipment", "Cleanliness", "Crowding", "Value"],
            "low": ["Trainer support", "Equipment condition", "Cleanliness", "Crowding", "Pricing"],
        },
    ),
    (
        ("school", "academy", "tuition", "coaching", "course", "training institute", "education"),
        {
            "high": ["Teaching quality", "Clear explanations", "Helpful faculty", "Course material", "Student support"],
            "mid": ["Teaching quality", "Faculty", "Course material", "Schedule", "Value"],
            "low": ["Teaching quality", "Communication", "Course material", "Schedule", "Fees"],
        },
    ),
    (
        ("car", "bike", "automotive", "garage", "vehicle", "repair", "detailing"),
        {
            "high": ["Repair quality", "Clear explanation", "Quick service", "Professional staff", "Fair pricing"],
            "mid": ["Service quality", "Communication", "Turnaround time", "Staff", "Pricing"],
            "low": ["Repair quality", "Communication", "Service delay", "Parts availability", "Pricing"],
        },
    ),
    (
        ("store", "shop", "retail", "boutique", "showroom", "products", "jewellery", "clothing"),
        {
            "high": ["Product quality", "Helpful staff", "Good selection", "Easy purchase", "Good value"],
            "mid": ["Product quality", "Staff help", "Selection", "Availability", "Value"],
            "low": ["Product quality", "Staff support", "Stock availability", "Returns", "Pricing"],
        },
    ),
    (
        ("photography", "photographer", "videography", "photo studio", "wedding shoot"),
        {
            "high": ["Photo quality", "Creative direction", "Professional team", "On-time delivery", "Editing quality"],
            "mid": ["Photo quality", "Communication", "Shoot experience", "Delivery time", "Value"],
            "low": ["Photo quality", "Communication", "Delivery delay", "Editing revisions", "Pricing"],
        },
    ),
)


def _description_from_record(scraped_context: str | None) -> str:
    if not scraped_context:
        return ""
    try:
        record = json.loads(scraped_context)
    except (TypeError, ValueError):
        return ""
    if not isinstance(record, dict) or record.get("version") != 1:
        return ""
    description = record.get("description")
    return description if isinstance(description, str) else ""


def suggest_review_tags(
    rating: int,
    business_name: str = "",
    custom_prompt: str | None = None,
    scraped_context: str | None = None,
) -> list[str]:
    """Return concise tags relevant to the stored owner/public business context."""
    tier = "high" if rating >= 4 else "mid" if rating == 3 else "low"
    context = " ".join(
        part for part in (business_name, custom_prompt or "", _description_from_record(scraped_context)) if part
    ).casefold()

    best_tags = None
    best_score = 0
    for keywords, tags_by_tier in INDUSTRIES:
        score = sum(1 for keyword in keywords if keyword in context)
        if score > best_score:
            best_score = score
            best_tags = tags_by_tier[tier]

    return list(best_tags or GENERIC_TAGS[tier])
