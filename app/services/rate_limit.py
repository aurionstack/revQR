from slowapi import Limiter
from slowapi.util import get_remote_address

# Create a rate limiter based on the user's IP address
limiter = Limiter(key_func=get_remote_address)
