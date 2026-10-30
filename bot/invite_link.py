"""Generate the reproducible Discord server-install (invite) link for the configured application; no token is involved.

The link requests the bot and applications.commands scopes with permission integer 0: every reply is an interaction
response, which needs no channel permission, so the Bot asks for nothing beyond membership (never Administrator).
"""

import argparse
import json
import sys
from urllib.parse import urlencode

from bot.acceptance_config import validate_snowflake

AUTHORIZE_URL = "https://discord.com/oauth2/authorize"
INVITE_SCOPES = ("bot", "applications.commands")  # [scopes]: Bot membership plus slash-command availability.
INVITE_PERMISSIONS = 0  # [minimal permissions]: Interaction responses need none; members still need "Use Application Commands".
GUILD_INSTALL = 0  # [integration_type]: 0 installs to a server; user installs are not offered.


def build_invite_url(application_id: int) -> str:
    """@description: Build the deterministic server-install URL a guild administrator opens to add the Bot.
    @param application_id: Public Discord application ID (not a secret).
    @return: OAuth2 authorize URL with fixed scopes and permissions.
    """
    query = urlencode({"client_id": validate_snowflake(application_id, "application_id"), "permissions": INVITE_PERMISSIONS,
                       "integration_type": GUILD_INSTALL, "scope": " ".join(INVITE_SCOPES)})
    return f"{AUTHORIZE_URL}?{query}"


def invite_summary(application_id: int) -> dict:
    """@description: Describe the link and the manual Developer Portal steps the code cannot perform.
    @param application_id: Public Discord application ID.
    @return: JSON-serializable summary.
    """
    return {"invite_url": build_invite_url(application_id), "scopes": list(INVITE_SCOPES), "permissions": INVITE_PERMISSIONS,
            "administrator_requested": False, "contains_token": False,
            "owner_portal_steps": [
                "Installation: enable Guild Install; disable User Install (commands are guild-only).",
                "Bot: turn Public Bot on so other server administrators can install it with this link; Privileged Gateway Intents stay off.",
                "General Information: leave Interactions Endpoint URL empty (the Bot uses the Gateway).",
                "Publish global commands once with python -m bot.command_sync (preview, then apply) after deployment.",
            ],
            "propagation_note": "New global commands or a newly joined guild may take some time before commands appear in clients."}


def main(argv: list[str] | None = None) -> int:
    """@description: Print the invite link and steps for the given application ID.
    @param argv: Optional argument list.
    @return: 0 on success.
    """
    parser = argparse.ArgumentParser(prog="python -m bot.invite_link", description="Print the JukeBox101 server-install link")
    parser.add_argument("--application-id", type=int, required=True)
    arguments = parser.parse_args(sys.argv[1:] if argv is None else argv)
    print(json.dumps(invite_summary(arguments.application_id), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
