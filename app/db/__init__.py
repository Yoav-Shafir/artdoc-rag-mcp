"""Database module: all SQL against the platform's MySQL database (read-only)."""

from app.db.database import Database, DatabaseError, FileRecord

__all__ = ["Database", "DatabaseError", "FileRecord"]
