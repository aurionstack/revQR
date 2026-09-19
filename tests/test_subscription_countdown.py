from pathlib import Path
import pytest

pytestmark = pytest.mark.no_db


def test_subscription_countdown_is_shown_in_owner_views():
    component = Path("app/templates/components/subscription_countdown.html").read_text(encoding="utf-8")
    script = Path("app/static/js/app.js").read_text(encoding="utf-8")
    assert "data-subscription-expiry" in component
    assert "days · " in script and "hours · " in script and "minutes" in script
    for name in ("home.html", "qr.html", "billing.html"):
        source = Path("app/templates/dashboard", name).read_text(encoding="utf-8")
        assert 'components/subscription_countdown.html' in source
