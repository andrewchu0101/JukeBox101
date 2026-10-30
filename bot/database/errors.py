"""Classify PostgreSQL driver failures for the application without exposing statement text or parameters.

The single driver-specific error hierarchy lives here so services and stores depend on stable names, not codes.
"""

import psycopg
from psycopg import errors as pg_errors

DatabaseError = psycopg.Error  # [driver failure root]: Every psycopg failure, including connection loss.
UniqueViolation = pg_errors.UniqueViolation  # [SQLSTATE 23505]: A unique or primary key already holds the value.


def is_unique_violation(error: BaseException, constraint_name: str | None = None) -> bool:
    """@description: Recognise a duplicate key, optionally for one named constraint, so only that expected race is handled.
    @param error: Exception raised by a statement.
    @param constraint_name: Constraint that must be the one violated; None accepts any unique violation.
    @return: True only for a unique violation on the expected constraint.
    """
    if not isinstance(error, UniqueViolation):
        return False
    if constraint_name is None:
        return True
    return getattr(error.diag, "constraint_name", None) == constraint_name
