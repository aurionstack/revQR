"""Optional provider error reporting with request bodies and identity excluded."""
from app.config import settings


def scrub_event(event, hint):
    for key in ("request","user","breadcrumbs","extra"):
        event.pop(key,None)
    return event


def configure_monitoring():
    if settings.SENTRY_DSN:
        import sentry_sdk
        sentry_sdk.init(dsn=settings.SENTRY_DSN,send_default_pii=False,
            include_local_variables=False,traces_sample_rate=0,
            before_send=scrub_event,environment=settings.ENVIRONMENT)
