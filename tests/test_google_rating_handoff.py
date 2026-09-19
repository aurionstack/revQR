import pytest

pytestmark = pytest.mark.no_db


def test_generated_review_explains_google_rating_handoff():
    # The production template must never imply that Google stars are prefilled.
    from pathlib import Path
    source = Path("app/templates/review/generated.html").read_text(encoding="utf-8")
    assert "choose <b>{{ rating }} stars</b>" in source
    assert "does not allow RevQR to preselect stars" in source
    assert "Copy {{ rating }}★ Review" in source
