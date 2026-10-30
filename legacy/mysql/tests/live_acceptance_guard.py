"""Pure guards for the private development-guild live acceptance: target database, single candidate update scope, and remote command comparison.

Test-support module only; nothing here connects to Discord or MySQL. The live launcher imports these checks and refuses to proceed when any of them fails.
"""

EXPECTED_HOST = "127.0.0.1"  # [isolated loopback]: The only host the acceptance may use.
EXPECTED_PORT = 3307  # [isolated port]: Never 3306, the existing MySQL80 service.
EXPECTED_DATABASE = "jukebox101_test"  # [disposable database]: The only schema the acceptance may write.
EXPECTED_USER = "jukebox101_test_exchange"  # [application account]: DML only, no DDL and no grant changes.
EXPECTED_COMMANDS = ("balance", "daily", "exchange", "history", "share")  # [guild scope]: The only commands the sync may publish.


class LiveGuardRefused(RuntimeError):
    """Refuse the live step before any connection or write; the message names the check only."""


def assert_isolated_target(host: str, port: int, database: str, user: str) -> None:
    """@description: Refuse any database destination other than the isolated test instance, so the bot process can never reach 3306 or another schema.
    @param host: Configured database host.
    @param port: Configured database port.
    @param database: Configured database name.
    @param user: Configured application account.
    @return: None; any mismatch raises LiveGuardRefused.
    """
    if (host, port, database, user) != (EXPECTED_HOST, EXPECTED_PORT, EXPECTED_DATABASE, EXPECTED_USER):
        raise LiveGuardRefused("DATABASE_TARGET_MISMATCH")


def assert_single_candidate_update(matched_rows: int, expected_track_id: str, track_id: str) -> None:
    """@description: Allow the acceptance's pending-to-verified update only for exactly one row whose platform identifier is the approved candidate.
    @param matched_rows: Rows the update statement matched.
    @param expected_track_id: Platform identifier of the approved candidate song.
    @param track_id: Platform identifier of the row about to be updated.
    @return: None; any other scope raises LiveGuardRefused before the write.
    """
    if track_id != expected_track_id:
        raise LiveGuardRefused("CANDIDATE_TRACK_MISMATCH")
    if matched_rows != 1:
        raise LiveGuardRefused("CANDIDATE_UPDATE_NOT_SINGLE_ROW")


def classify_remote_commands(remote_names: tuple) -> str:
    """@description: Classify the read-only remote command list of the guild before any sync, so existing commands are reported and never overwritten.
    @param remote_names: Command names currently registered in the guild, as returned by a read-only query.
    @return: empty when none exist, exact when they equal the approved five, otherwise other (sync must stop).
    """
    if remote_names == ():
        return "empty"
    if tuple(sorted(remote_names)) == tuple(sorted(EXPECTED_COMMANDS)):
        return "exact"
    return "other"


EXPECTED_GUILD_ID = 1559749813011087392  # [approved private guild]: Supplied by the Owner for this acceptance only.
EXPECTED_APPLICATION_ID = 1559751168723521576  # [approved test application]: Supplied by the Owner for this acceptance only.
EXPECTED_OWNER_ID = 456708412697215018  # [Owner and sole tester]: Supplied by the Owner for this acceptance only.


def assert_candidate_before_update(row: dict, expected_source_url: str, expected_track_id: str) -> None:
    """@description: Accept the single verified update only for the pending approved intake: the approved source, the approved track, no delivery link yet.
    @param row: Current state of the candidate row read before the update.
    @param expected_source_url: The approved candidate URL (C).
    @param expected_track_id: The approved platform identifier of C.
    @return: None; any other state raises LiveGuardRefused before the write.
    """
    if (row.get("platform"), row.get("platform_track_id"), row.get("source_url")) != ("youtube", expected_track_id, expected_source_url):
        raise LiveGuardRefused("CANDIDATE_IDENTITY_MISMATCH")
    if row.get("resolution_status") != "pending" or row.get("youtube_url") is not None:
        raise LiveGuardRefused("CANDIDATE_NOT_PENDING_INTAKE")


def assert_settlement_scope(requested: set, approved: set) -> None:
    """@description: Settle exactly the approved operations during the settlement window, never a batch.
    @param requested: Operation identifiers the window is about to settle.
    @param approved: The operation identifiers approved for this acceptance (C and the Owner's D).
    @return: None; any extra or missing operation raises LiveGuardRefused.
    """
    if set(requested) != set(approved):
        raise LiveGuardRefused("SETTLEMENT_SCOPE_MISMATCH")


def expected_intake_control() -> dict:
    """@description: The control state the intake path requires: reviewed flags on, settlement off, counters at zero for a new singleton.
    @return: Expected control values.
    """
    return {"schema_version": "2b2-b1", "accepted_sequence": 0, "revision": 0,
            "snapshot_ready": 1, "legacy_writers_quiesced": 1, "settlement_enabled": 0}


def assert_control_transition(before: dict, after: dict, allowed_fields: set) -> None:
    """@description: A control change may touch only the named switch; counters and every other field must be identical.
    @param before: Control values read before the change.
    @param after: Control values read after the change.
    @param allowed_fields: The only field names the change is permitted to alter.
    @return: None; any other difference raises LiveGuardRefused.
    """
    for key in set(before) | set(after):
        if key not in allowed_fields and before.get(key) != after.get(key):
            raise LiveGuardRefused("CONTROL_UNEXPECTED_CHANGE")


def assert_discord_identifiers(guild_id: int, application_id: int, owner_id: int) -> None:
    """@description: The guild, application and Owner identifiers must be exactly the approved values before any login or sync.
    @param guild_id: Configured guild identifier.
    @param application_id: Configured application identifier.
    @param owner_id: Configured Owner identifier.
    @return: None; any difference raises LiveGuardRefused.
    """
    if (guild_id, application_id, owner_id) != (EXPECTED_GUILD_ID, EXPECTED_APPLICATION_ID, EXPECTED_OWNER_ID):
        raise LiveGuardRefused("DISCORD_IDENTIFIER_MISMATCH")


LIVE_SCHEMA_DATABASE = "jukebox101_live_20261025"  # [approved dedicated schema]: The only additional schema this live run may use.


def assert_live_schema_target(host: str, port: int, database: str, user: str) -> None:
    """@description: Accept exactly the approved dedicated schema on the isolated loopback instance with the exchange account; every other destination is refused.
    @param host: Configured database host.
    @param port: Configured database port.
    @param database: Configured database name.
    @param user: Configured database account.
    @return: None; any mismatch raises LiveGuardRefused.
    """
    if (host, port, database, user) != (EXPECTED_HOST, EXPECTED_PORT, LIVE_SCHEMA_DATABASE, EXPECTED_USER):
        raise LiveGuardRefused("LIVE_SCHEMA_TARGET_MISMATCH")
