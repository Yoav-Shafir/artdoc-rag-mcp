"""Read-only access to the platform's MySQL database (overstone_v2).

All SQL used by the RAG app lives here. The `file` table is the source of
truth for which files exist and who owns them. S3 object keys are
`file.fileId` (`<uuid>.<ext>`), so a FileRecord's `file_id` is also its
storage key.

Ownership: file -> asset (file.entityId) -> account (asset.accountId).
Files are only attached to assets, and deleted assets are ignored.

Access is read-only: the session is put in READ ONLY mode on connect.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

import pymysql
from pymysql.cursors import DictCursor

from app.config import config

logger = logging.getLogger(__name__)

# All PDF files of an account's (non-deleted) assets. Image categories are skipped.
# '%%' is a literal '%' for pymysql, so MySQL receives LIKE '%.pdf'.
_FILES_FOR_ACCOUNT_SQL = """
    SELECT f.fileId AS file_id, f.name, f.category, f.updatedAt AS updated_at,
           a.id AS asset_id, a.name AS asset_name, a.accountId AS account_id
    FROM asset a
    JOIN file f ON f.entityType = 'ASSET' AND f.entityId = a.id
    WHERE a.accountId = %(account_id)s AND a.deleted = 0
      AND f.category NOT IN ('IMAGES', 'CR_IMAGE')
      AND f.fileId LIKE '%%.pdf'
    ORDER BY f.fileId
"""

# One file by fileId. LEFT JOIN: a file whose asset is missing or deleted still
# returns a row, with account_id = None, so resolve_owner() can say why.
_LOOKUP_FILE_SQL = """
    SELECT f.fileId AS file_id, f.name, f.category, f.updatedAt AS updated_at,
           f.entityId AS asset_id, a.name AS asset_name, a.accountId AS account_id
    FROM file f
    LEFT JOIN asset a ON a.id = f.entityId AND a.deleted = 0
    WHERE f.fileId = %(file_id)s AND f.entityType = 'ASSET'
"""

# Every account that has at least one file on a (non-deleted) asset.
# {account_filter} is empty, or limits the result to one account (pilot runs).
_ACCOUNT_IDS_SQL = """
    SELECT DISTINCT a.accountId AS account_id
    FROM asset a
    JOIN file f ON f.entityType = 'ASSET' AND f.entityId = a.id
    WHERE a.accountId IS NOT NULL AND a.deleted = 0 {account_filter}
    ORDER BY account_id
"""


class DatabaseError(Exception):
    """Raised when a file can't be resolved to an owner, or a query fails."""


@dataclass(frozen=True)
class FileRecord:
    """One `file` row plus its asset and owning account."""

    file_id: str  # also the S3 key
    name: str  # original file name, for citations
    category: str
    asset_id: int  # the asset the file is attached to (file.entityId)
    account_id: Optional[int]  # None = asset missing or deleted: never ingest
    asset_name: Optional[str] = None
    updated_at: Optional[datetime] = None

    @property
    def extension(self) -> str:
        """Lower-case extension including the dot, e.g. '.pdf' ('' if none)."""
        _, dot, ext = self.file_id.rpartition(".")
        return f".{ext.lower()}" if dot else ""


class Database:
    """Read-only access to the platform database. Holds every query the app needs."""

    def __init__(self, cfg: Optional[Any] = None):
        """Initialize the database access (connects lazily on first query).

        Args:
            cfg: Database settings (uses global config if None)
        """
        self.cfg = cfg or config.db
        self._connection: Optional[pymysql.connections.Connection] = None

    def _connect(self) -> pymysql.connections.Connection:
        if self._connection is None or not self._connection.open:
            try:
                self._connection = pymysql.connect(
                    host=self.cfg.hostname,
                    port=self.cfg.port,
                    user=self.cfg.username,
                    password=self.cfg.password,
                    database=self.cfg.db_name,
                    cursorclass=DictCursor,
                    autocommit=True,
                    connect_timeout=5,
                )
                with self._connection.cursor() as cursor:
                    # Guard rail: this service must never write to the platform DB.
                    cursor.execute("SET SESSION TRANSACTION READ ONLY")
            except pymysql.MySQLError as e:
                raise DatabaseError(f"Failed to connect to database: {e}") from e
        return self._connection

    def _query(self, sql: str, params: Optional[dict] = None) -> list[dict]:
        try:
            with self._connect().cursor() as cursor:
                cursor.execute(sql, params)
                return list(cursor.fetchall())
        except pymysql.MySQLError as e:
            raise DatabaseError(f"Database query failed: {e}") from e

    def list_account_ids(self, account_id: Optional[int] = None) -> list[int]:
        """Every account that owns at least one file, or only `account_id` if given."""
        if account_id is None:
            sql, params = _ACCOUNT_IDS_SQL.format(account_filter=""), None
        else:
            sql = _ACCOUNT_IDS_SQL.format(account_filter="AND a.accountId = %(account_id)s")
            params = {"account_id": account_id}
        return [row["account_id"] for row in self._query(sql, params)]

    def files_for_account(self, account_id: int) -> list[FileRecord]:
        """All file records owned by an account, loaded in one query."""
        rows = self._query(_FILES_FOR_ACCOUNT_SQL, {"account_id": account_id})
        logger.info(f"Loaded {len(rows)} file records for account {account_id}")
        return [FileRecord(**row) for row in rows]

    def lookup_file(self, file_id: str) -> Optional[FileRecord]:
        """Find one file record by fileId (= S3 key), or None if unknown."""
        rows = self._query(_LOOKUP_FILE_SQL, {"file_id": file_id})
        return FileRecord(**rows[0]) if rows else None

    def resolve_owner(self, file_id: str) -> FileRecord:
        """Like lookup_file(), but fail closed: raise unless the file has an owner.

        Raises:
            DatabaseError: If the file is unknown or has no resolvable account
        """
        entry = self.lookup_file(file_id)
        if entry is None:
            raise DatabaseError(f"{file_id}: not in file table")
        if entry.account_id is None:
            raise DatabaseError(f"{file_id}: no owner (asset {entry.asset_id} missing or deleted)")
        return entry

    def category_ids(self, category_type: str) -> dict[str, int]:
        """Readable names -> ids for one kind of category, e.g. 'medium' or 'period'.

        asset.medium and asset.period store a category.id; this returns e.g.
        {"PAINTING": 6, "PAPER/WATERCOLOR": 7, ...} so names can be turned into ids.
        """
        rows = self._query(
            "SELECT id, name FROM category WHERE type = %(type)s ORDER BY id",
            {"type": category_type},
        )
        return {row["name"]: row["id"] for row in rows}

    def candidate_asset_ids(self, account_id: int, filters) -> list[int]:
        """IDs of one account's assets that pass every filter the planner filled in.

        `filters` is the planner's AssetFilters (not imported here, to keep this
        module free of planner code). Empty filters are skipped; accountId and
        deleted = 0 are always applied, whatever the planner says.
        """
        # Always-on conditions: the tenant and no deleted assets.
        conditions = ["a.accountId = %(account_id)s", "a.deleted = 0"]
        params = {"account_id": account_id}

        # One SQL condition per filled-in filter. Values are always passed as
        # parameters (%(name)s), never pasted into the SQL text.
        if filters.asset_type is not None:
            conditions.append("a.type = %(asset_type)s")
            params["asset_type"] = filters.asset_type.value  # e.g. 'FINE_ART'

        if filters.artist is not None:
            # The user may write 'Picasso' for 'Pablo Picasso': match any maker whose
            # name contains it (the column's collation also ignores case and accents).
            conditions.append(
                "a.makerId IN (SELECT id FROM maker WHERE name LIKE %(artist)s)"
            )
            params["artist"] = f"%{filters.artist}%"

        if filters.year_from is not None:
            conditions.append("YEAR(a.year) >= %(year_from)s")  # asset.year is a datetime
            params["year_from"] = filters.year_from
        if filters.year_to is not None:
            conditions.append("YEAR(a.year) <= %(year_to)s")
            params["year_to"] = filters.year_to

        # asset.medium / asset.period store a category.id: look the id up by name.
        if filters.medium is not None:
            conditions.append(
                "a.medium IN (SELECT id FROM category WHERE type = 'medium' AND name = %(medium)s)"
            )
            params["medium"] = filters.medium.value  # e.g. 'PAINTING'
        if filters.period is not None:
            conditions.append(
                "a.period IN (SELECT id FROM category WHERE type = 'period' AND name = %(period)s)"
            )
            params["period"] = filters.period.value  # e.g. 'POST-WAR (1945-1970)'

        if filters.condition_status is not None:
            conditions.append("a.conditionStatus = %(condition_status)s")
            params["condition_status"] = filters.condition_status.value  # e.g. 'VERY_GOOD'

        sql = "SELECT a.id FROM asset a WHERE " + " AND ".join(conditions) + " ORDER BY a.id"
        return [row["id"] for row in self._query(sql, params)]

    def asset_names(self, asset_ids: list[int]) -> dict[int, str]:
        """{assetId: name} for the given assets, in one query."""
        if not asset_ids:  # `IN ()` is invalid SQL
            return {}
        # pymysql turns the tuple into a parenthesised list: IN (7159, 6638, ...)
        rows = self._query(
            "SELECT id, name FROM asset WHERE id IN %(ids)s", {"ids": tuple(asset_ids)}
        )
        return {row["id"]: row["name"] for row in rows}

    def close(self) -> None:
        if self._connection is not None and self._connection.open:
            self._connection.close()
        self._connection = None
