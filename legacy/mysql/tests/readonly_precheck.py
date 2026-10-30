"""Read-only precheck helpers for the approved live run: a fixed list of SELECT and SHOW statements over the verified isolated connection, a
REST-only Discord identity and command comparison, and a closing shutdown of the isolated instance this run started.

Every SQL statement is a constant in this module. No data is written, no flag is switched, no grant is created, and the Discord side never
opens a gateway or synchronizes commands. Secrets are passed in as arguments by a local launcher that reads them hidden.
"""

import time

from bot.guild_acceptance import login_smoke, read_only_remote_report

READ_ONLY_SESSION = "SET SESSION TRANSACTION READ ONLY"  # [session guard]: Any later write on this connection is refused by the server.
GRANTS_QUERY = "SHOW GRANTS FOR CURRENT_USER()"
CONTROL_QUERY = ("SELECT singleton_id, schema_version, accepted_sequence, revision, snapshot_ready, legacy_writers_quiesced, "
                 "settlement_enabled FROM jb2_control WHERE singleton_id = 1")
POOL_QUERY = ("SELECT e.discord_user_id AS submitter, COUNT(*) AS settled_versions, "
              "SUM(s.resolution_status = 'verified' AND s.youtube_url IS NOT NULL) AS deliverable "
              "FROM jb2_claims c JOIN jb2_events e ON e.operation_id = c.operation_id "
              "JOIN jb2_videos v ON v.video_id = e.video_id JOIN songs s ON s.song_id = v.song_id "
              "GROUP BY e.discord_user_id ORDER BY e.discord_user_id")
BALANCE_QUERY = "SELECT credit_balance FROM users WHERE discord_user_id = %s"
OPEN_QUERY = "SELECT status, COUNT(*) AS total FROM recommendations WHERE discord_user_id = %s GROUP BY status"
COMPENSATION_QUERY = ("SELECT TABLE_NAME AS table_name FROM information_schema.TABLES WHERE table_schema = DATABASE() AND table_name IN "
                      "('exchange_incidents', 'exchange_redelivery_grants', 'exchange_incident_events')")
EVENTS_TOTAL_QUERY = "SELECT COUNT(*) AS total FROM jb2_events"  # [priority blockers]: Any earlier accepted operation could block a candidate.
USER_ROW_QUERY = "SELECT discord_user_id, credit_balance FROM users WHERE discord_user_id = %s"
RECEIPT_QUERY = "SELECT operation_id FROM jb2_receipts WHERE operation_id = %s"
EVENT_QUERY = "SELECT operation_id FROM jb2_events WHERE operation_id = %s"
DECISION_QUERY = "SELECT decision_id FROM jb2_decisions WHERE decision_id = %s"
ALLOWED_PREFIXES = ("SET SESSION TRANSACTION READ ONLY", "SHOW GRANTS", "SELECT ")  # [allowlist]: Every statement this module may send.


def require_interactive(stdin_is_tty: bool) -> None:
    """@description: Refuse hidden-input entry when no terminal is attached, because a plain fallback would echo the secret.
    @param stdin_is_tty: Whether standard input is an interactive terminal.
    @return: None; raises SystemExit with a code-only message otherwise.
    """
    if stdin_is_tty is not True:
        raise SystemExit("HIDDEN_INPUT_REQUIRES_A_TERMINAL")


def run_read_only_snapshot(connection, owner_id: int, synthetic_id: int, operation_ids: tuple, decision_ids: tuple) -> dict:
    """@description: Run the fixed read-only statements on one verified connection and return their results without any secret.
    @param connection: Verified isolated connection whose server identity has already been checked.
    @param owner_id: Configured Owner identifier, bound as a parameter.
    @param synthetic_id: Approved synthetic submitter identifier, checked for occupancy only.
    @param operation_ids: Operation identifiers planned for this run, checked for occupancy only.
    @param decision_ids: Decision identifiers planned for this run, checked for occupancy only.
    @return: Grants, control row, settled pool, Owner and synthetic user rows, occupancy of planned identifiers, Owner open deliveries and compensation table presence.
    """
    snapshot = {}
    with connection.cursor() as cursor:
        cursor.execute(READ_ONLY_SESSION)
        cursor.execute(GRANTS_QUERY)
        snapshot["grants"] = [list(row.values())[0] for row in cursor.fetchall()]
        cursor.execute(CONTROL_QUERY)
        snapshot["control"] = cursor.fetchone()
        cursor.execute(POOL_QUERY)
        snapshot["settled_pool"] = cursor.fetchall()
        cursor.execute(BALANCE_QUERY, (owner_id,))
        snapshot["owner_balance"] = cursor.fetchone()
        cursor.execute(OPEN_QUERY, (owner_id,))
        snapshot["owner_deliveries"] = cursor.fetchall()
        cursor.execute(EVENTS_TOTAL_QUERY)
        events_row = cursor.fetchone()
        snapshot["events_total"] = int(events_row["total"]) if events_row else 0
        cursor.execute(USER_ROW_QUERY, (synthetic_id,))
        snapshot["synthetic_user_row"] = cursor.fetchone()
        occupied = {"operations": [], "decisions": []}
        for operation_id in operation_ids:
            cursor.execute(RECEIPT_QUERY, (operation_id,))
            receipt_found = cursor.fetchone() is not None
            cursor.execute(EVENT_QUERY, (operation_id,))
            event_found = cursor.fetchone() is not None
            if receipt_found or event_found:
                occupied["operations"].append(operation_id)
        for decision_id in decision_ids:
            cursor.execute(DECISION_QUERY, (decision_id,))
            if cursor.fetchone() is not None:
                occupied["decisions"].append(decision_id)
        snapshot["occupied_identifiers"] = occupied
        cursor.execute(COMPENSATION_QUERY)
        snapshot["compensation_tables"] = sorted(row["table_name"] for row in cursor.fetchall())
    return snapshot


async def discord_precheck(client, settings) -> dict:
    """@description: Log in over REST only, verify the application and guild identity, and compare remote commands with the approved registry.
    @param client: Database-disabled client whose command tree is the approved registry.
    @param settings: Expected application, development guild and token.
    @return: Identity summary and the read-only comparison report.
    """
    try:
        summary = await login_smoke(client, settings, authorized=True)
        report = await read_only_remote_report(client, settings.guild_id)
    finally:
        await client.close()
    return {"commands_local": summary["commands"], "report": report}


def shutdown_isolated_instance(root_connection, port_is_listening, wait_seconds: int = 60) -> dict:
    """@description: Shut down the isolated instance this run started, then wait until its port stops listening.
    @param root_connection: Administrative connection to the same verified isolated instance, used only for SHUTDOWN.
    @param port_is_listening: Callable that reports whether the isolated port still accepts connections.
    @param wait_seconds: Upper bound for waiting on the port to close.
    @return: Result with the shutdown outcome and whether the port closed.
    """
    with root_connection.cursor() as cursor:
        cursor.execute("SHUTDOWN")
    deadline = time.monotonic() + wait_seconds
    listening = port_is_listening()
    while listening and time.monotonic() < deadline:
        time.sleep(1)
        listening = port_is_listening()
    return {"shutdown_sent": True, "port_closed": not listening}
