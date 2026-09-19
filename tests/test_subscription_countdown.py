from pathlib import Path
import pytest

pytestmark = pytest.mark.no_db


def test_subscription_status_is_shown_in_owner_views():
    component = Path("app/templates/components/subscription_countdown.html").read_text(encoding="utf-8")
    assert '>Active<' in component
    assert "Calculating" not in component
    for name in ("home.html", "qr.html"):
        source = Path("app/templates/dashboard", name).read_text(encoding="utf-8")
        assert 'components/subscription_countdown.html' in source
    billing = Path("app/templates/dashboard/billing.html").read_text(encoding="utf-8")
    assert 'components/subscription_countdown.html' not in billing
