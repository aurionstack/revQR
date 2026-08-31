import pytest
from sqlalchemy import inspect

from app.models import Business


pytestmark = pytest.mark.no_db


@pytest.mark.parametrize(
    "relationship_name",
    ["payments", "reviews", "scans", "feedback_items"],
)
def test_business_children_rely_on_database_delete_cascade(relationship_name):
    relationship = inspect(Business).relationships[relationship_name]
    assert relationship.passive_deletes == "all"
