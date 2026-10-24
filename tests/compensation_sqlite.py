"""Offline SQLite stand-in for the compensation candidate schema, so the production SQL text runs on a second engine.

Only test code. The adapter translates %s placeholders, strips FOR UPDATE and ignores SET statements (session settings have no SQLite
meaning); every other statement reaches SQLite unchanged. The schema mirrors the candidate MySQL constraints that these tests exercise.
Nothing here connects to MySQL.
"""

import sqlite3
from datetime import datetime
from pathlib import Path

sqlite3.register_adapter(datetime, lambda value: value.isoformat(" "))  # [explicit adapter]: Lexical order equals time order; avoids the deprecated default.
sqlite3.register_converter("DATETIME", lambda raw: datetime.fromisoformat(raw.decode("ascii")))  # [DATETIME]: Returns datetimes as MySQL does, so age arithmetic is the same.

SCHEMA = """
CREATE TABLE jb2_control (singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1), schema_version TEXT NOT NULL);
CREATE TABLE users (discord_user_id INTEGER PRIMARY KEY, credit_balance INTEGER NOT NULL DEFAULT 0 CHECK (credit_balance >= 0));
CREATE TABLE songs (song_id INTEGER PRIMARY KEY AUTOINCREMENT, platform TEXT NOT NULL DEFAULT 'youtube',
    platform_track_id TEXT NOT NULL UNIQUE, source_url TEXT NOT NULL, youtube_url TEXT,
    resolution_status TEXT NOT NULL DEFAULT 'pending', verification_provenance TEXT);
CREATE TABLE recommendations (recommendation_id INTEGER PRIMARY KEY AUTOINCREMENT, discord_user_id INTEGER NOT NULL,
    song_id INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'reserved' CHECK (status IN ('reserved','sending','delivered','failed','uncertain')),
    discord_message_id INTEGER, delivered_at TEXT, created_at TEXT NOT NULL, claim_token TEXT, claimed_at TEXT, released_at TEXT,
    CHECK (released_at IS NULL OR status = 'failed'),
    CHECK (status <> 'sending' OR (claim_token IS NOT NULL AND claimed_at IS NOT NULL)));
CREATE UNIQUE INDEX uq_recommendations_active ON recommendations (discord_user_id, song_id) WHERE released_at IS NULL;
CREATE TABLE credit_ledger (ledger_id INTEGER PRIMARY KEY AUTOINCREMENT, discord_user_id INTEGER NOT NULL,
    credit_delta INTEGER NOT NULL, reason TEXT NOT NULL CHECK (reason IN ('submission_award','exchange_debit','delivery_refund')),
    idempotency_key TEXT NOT NULL UNIQUE, awarded_song_id INTEGER UNIQUE, recommendation_id INTEGER,
    created_at TEXT, UNIQUE (recommendation_id, reason));
CREATE TABLE exchange_incidents (incident_id INTEGER PRIMARY KEY AUTOINCREMENT, recommendation_id INTEGER NOT NULL UNIQUE,
    debit_ledger_id INTEGER NOT NULL UNIQUE, discord_user_id INTEGER NOT NULL, song_id INTEGER NOT NULL, reason_code TEXT NOT NULL,
    approved_by INTEGER NOT NULL, approved_at DATETIME NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('approved','settled','void','not_system_fault','non_fault_refunded')),
    refund_status TEXT NOT NULL DEFAULT 'none' CHECK (refund_status IN ('none','refunded_once')),
    refund_ledger_id INTEGER UNIQUE, redelivery_decision TEXT NOT NULL DEFAULT 'pending'
        CHECK (redelivery_decision IN ('pending','approved','not_approved')),
    settled_at DATETIME, closure TEXT CHECK (closure IS NULL OR closure IN ('refund_only','refund_and_redelivery','non_fault_refund')), closed_at TEXT,
    CONSTRAINT chk_incidents_refund CHECK ((refund_status = 'refunded_once') = (refund_ledger_id IS NOT NULL)),
    CONSTRAINT chk_incidents_settled CHECK (state <> 'settled' OR (refund_status = 'refunded_once' AND settled_at IS NOT NULL
        AND redelivery_decision <> 'pending' AND closure IS NOT NULL)),
    CONSTRAINT chk_incidents_non_fault CHECK (state <> 'non_fault_refunded' OR (refund_status = 'refunded_once' AND closure = 'non_fault_refund'
        AND redelivery_decision = 'not_approved' AND settled_at IS NOT NULL AND closed_at IS NOT NULL)),
    CONSTRAINT chk_incidents_closed CHECK (closed_at IS NULL OR state IN ('settled','void','non_fault_refunded')));
CREATE TABLE exchange_redelivery_grants (grant_id INTEGER PRIMARY KEY AUTOINCREMENT, incident_id INTEGER NOT NULL UNIQUE,
    discord_user_id INTEGER NOT NULL, song_id INTEGER NOT NULL, granted_at DATETIME NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('open','claimed','sent','not_sent','uncertain')), claim_token TEXT, claimed_at TEXT,
    delivery_fact TEXT NOT NULL DEFAULT 'none' CHECK (delivery_fact IN ('none','sent','not_sent','unknown')),
    message_id INTEGER, delivered_at TEXT,
    owner_disposition TEXT NOT NULL DEFAULT 'none' CHECK (owner_disposition IN ('none','no_further_action','confirmed_delivered','confirmed_not_delivered')),
    disposition_by INTEGER, disposition_at TEXT, closed_at TEXT,
    holds_song INTEGER NOT NULL DEFAULT 1,
    CONSTRAINT chk_grants_claim CHECK (state <> 'claimed' OR claim_token IS NOT NULL),
    CONSTRAINT chk_grants_sent CHECK (state <> 'sent' OR delivery_fact = 'sent'),
    CONSTRAINT chk_grants_holds CHECK (holds_song IN (0, 1)));
CREATE INDEX ix_redelivery_user_open ON exchange_redelivery_grants (discord_user_id, closed_at);
CREATE INDEX ix_incidents_user ON exchange_incidents (discord_user_id, incident_id);
CREATE TABLE exchange_incident_events (event_id INTEGER PRIMARY KEY AUTOINCREMENT, incident_id INTEGER NOT NULL,
    action TEXT NOT NULL, actor INTEGER, at TEXT NOT NULL, detail TEXT NOT NULL);
"""


class AdapterCursor:
    """Forward one SQLite cursor, translating only the MySQL-specific syntax the production SQL uses."""

    def __init__(self, cursor) -> None:
        """@description: Wrap one real SQLite cursor.
        @param cursor: Real cursor.
        @return: None.
        """
        self._cursor = cursor
        self.catalogue_dialect = "sqlite"  # [catalogue family]: Lets the schema guard read SQLite's catalogue instead of information_schema.

    def __enter__(self):
        """@description: Support the context-manager form used by the production code.
        @return: This cursor.
        """
        return self

    def __exit__(self, *details) -> None:
        """@description: Nothing to release beyond the connection.
        @param details: Exception details, unused.
        @return: None.
        """
        return None

    def execute(self, sql: str, args=()) -> None:
        """@description: Translate placeholders and session syntax, then run the statement.
        @param sql: Production statement text.
        @param args: Bound values.
        @return: None.
        """
        statement = sql.strip()
        if statement.upper().startswith("SET "):
            self._cursor.execute("SELECT 1 WHERE 0")  # [session setting]: SQLite has none; an empty result keeps fetch calls valid.
            return
        self._cursor.execute(statement.replace(" FOR UPDATE", "").replace("%s", "?"), tuple(args or ()))

    def fetchone(self):
        """@description: Return the next row with name access.
        @return: Row or None.
        """
        return self._cursor.fetchone()

    def fetchall(self) -> list:
        """@description: Return the remaining rows with name access.
        @return: Rows.
        """
        return self._cursor.fetchall()

    def __getattr__(self, name: str):
        """@description: Delegate other attributes such as rowcount and lastrowid.
        @param name: Attribute name.
        @return: The real cursor's attribute.
        """
        return getattr(self._cursor, name)


class AdapterConnection:
    """Expose the transaction methods the production transaction boundary calls, over one SQLite file."""

    def __init__(self, path: Path) -> None:
        """@description: Open one connection in explicit-transaction mode.
        @param path: SQLite database file shared by every connection of one test.
        @return: None.
        """
        self._connection = sqlite3.connect(str(path), isolation_level=None, timeout=5, detect_types=sqlite3.PARSE_DECLTYPES)
        self._connection.row_factory = sqlite3.Row

    def cursor(self, *args, **kwargs) -> AdapterCursor:
        """@description: Return a translating cursor.
        @param args: Unused cursor options.
        @param kwargs: Unused cursor options.
        @return: Translating cursor.
        """
        return AdapterCursor(self._connection.cursor())

    def begin(self) -> None:
        """@description: Start an explicit transaction.
        @return: None.
        """
        self._connection.execute("BEGIN")

    def commit(self) -> None:
        """@description: Commit the explicit transaction.
        @return: None.
        """
        self._connection.execute("COMMIT")

    def rollback(self) -> None:
        """@description: Roll back when a transaction is open.
        @return: None.
        """
        if self._connection.in_transaction:
            self._connection.execute("ROLLBACK")

    def close(self) -> None:
        """@description: Close the underlying connection.
        @return: None.
        """
        self._connection.close()


def create_database(path: Path, schema: str = SCHEMA) -> None:
    """@description: Create the mirrored schema and the single control row the production transaction requires.
    @param path: Database file to create.
    @param schema: Schema text; a deliberately altered text tests the schema guard's refusals.
    @return: None.
    """
    connection = sqlite3.connect(str(path))
    connection.executescript(schema)
    connection.execute("INSERT INTO jb2_control (singleton_id, schema_version) VALUES (1, '2b2-b1')")
    connection.commit()
    connection.close()


def factory_for(path: Path):
    """@description: Build the connection factory the production repository expects.
    @param path: Database file shared by the test.
    @return: Zero-argument factory returning a fresh adapter connection.
    """
    return lambda: AdapterConnection(path)
