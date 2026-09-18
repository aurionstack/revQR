from slowapi import Limiter
from slowapi.util import get_remote_address
from app.config import settings

# Create a rate limiter based on the user's IP address
limiter = Limiter(key_func=get_remote_address, storage_uri=settings.RATE_LIMIT_STORAGE_URI)
