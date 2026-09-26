"""The planner's hard-coded value lists must match the database.

If the platform adds an asset type, a condition, a medium or a period, these
tests fail, telling us to update app/planner.py.
"""

import re

import pytest

from app.db import Database, DatabaseError
from app.planner import AssetType, ConditionStatus, Medium, Period

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def db():
    database = Database()
    try:
        database.list_account_ids()
    except DatabaseError as e:
        pytest.skip(f"database not reachable: {e}")
    yield database
    database.close()


def enum_column_values(db, column: str) -> set[str]:
    """The allowed values of a MySQL enum column, e.g. asset.type."""
    row = db._query(
        "SELECT column_type AS column_type FROM information_schema.columns "
        "WHERE table_schema = DATABASE() AND table_name = 'asset' AND column_name = %(column)s",
        {"column": column},
    )[0]
    # column_type looks like: enum('FINE_ART','WINE',...)
    return set(re.findall(r"'([^']*)'", row["column_type"]))


def test_asset_type_matches_db(db):
    assert {member.value for member in AssetType} == enum_column_values(db, "type")


def test_condition_status_matches_db(db):
    assert {member.value for member in ConditionStatus} == enum_column_values(db, "conditionStatus")


def test_medium_matches_category_table(db):
    assert {member.value for member in Medium} == set(db.category_ids("medium"))


def test_period_matches_category_table(db):
    assert {member.value for member in Period} == set(db.category_ids("period"))
