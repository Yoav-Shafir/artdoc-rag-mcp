"""Integration tests for Database against the local dev MySQL (read-only).

Relies on dev data that exists today; skipped if MySQL isn't reachable.
"""

import pytest

from app.db import Database, DatabaseError

pytestmark = pytest.mark.integration

KNOWN_PDF = "062d2aed-2cab-481f-8781-51171b93451f.pdf"  # ZOST0001.pdf, account 46
UNKNOWN = "00000000-0000-0000-0000-000000000000.pdf"


@pytest.fixture(scope="module")
def db():
    c = Database()
    try:
        c.list_account_ids()
    except DatabaseError as e:
        pytest.skip(f"database not reachable: {e}")
    yield c
    c.close()


def test_lookup_known_file(db):
    entry = db.lookup_file(KNOWN_PDF)

    assert entry is not None
    assert entry.account_id == 46
    assert entry.name == "ZOST0001.pdf"
    assert entry.category == "VALUATION_REPORTS"
    assert entry.asset_id == 7442
    assert entry.extension == ".pdf"


def test_lookup_unknown_returns_none(db):
    assert db.lookup_file(UNKNOWN) is None


def test_resolve_owner_refuses_unknown_file(db):
    with pytest.raises(DatabaseError, match="not in file table"):
        db.resolve_owner(UNKNOWN)


def test_resolve_owner_refuses_file_whose_asset_is_missing(db):
    orphans = db._query(
        "SELECT f.fileId FROM file f LEFT JOIN asset a ON a.id = f.entityId "
        "WHERE f.entityType = 'ASSET' AND a.id IS NULL LIMIT 1"
    )
    if not orphans:
        pytest.skip("no orphaned file rows in this DB")

    with pytest.raises(DatabaseError, match="no owner"):
        db.resolve_owner(orphans[0]["fileId"])


def test_files_for_account_only_returns_that_account(db):
    files = db.files_for_account(46)

    assert files
    assert all(f.account_id == 46 for f in files)
    assert KNOWN_PDF in {f.file_id for f in files}
    assert len({f.file_id for f in files}) == len(files)  # no duplicates


def test_list_account_ids_includes_owner(db):
    assert 46 in db.list_account_ids()


def test_session_is_read_only(db):
    with pytest.raises(DatabaseError, match="READ ONLY"):
        db._query("UPDATE file SET name = name WHERE id = -1")
