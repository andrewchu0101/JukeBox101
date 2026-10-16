"""Expose explicit offline, login-only and development-guild sync acceptance commands.

The existing Discord client is always closed, while persistence and gateway processing remain disabled.
"""

import argparse
from bot.acceptance_config import load_development_guild_settings
from bot.acceptance_runtime import execute_acceptance
from bot.guild_acceptance import AcceptanceError, build_acceptance_client, guild_sync, local_preflight, login_smoke


def argument_parser() -> argparse.ArgumentParser:
    """@description: Keep network operations separate from the default local preflight.
    @param: none
    @return: Parser with no global-sync or database option.
    """
    parser = argparse.ArgumentParser(description="Database-disabled development guild acceptance")
    parser.add_argument("mode", choices=("preflight", "login", "guild-sync"), nargs="?", default="preflight")
    parser.add_argument("--authorize-login", action="store_true")
    parser.add_argument("--authorize-guild-sync", action="store_true")
    return parser


async def run_acceptance(arguments: argparse.Namespace) -> dict:
    """@description: Own client cleanup for preflight, failed login, cancelled sync and successful completion.
    @param arguments: Parsed explicit mode and authorization switches.
    @return: Public summary without credentials.
    """
    _validate_mode_authorization(arguments)
    settings = load_development_guild_settings()
    async with build_acceptance_client() as client:
        if arguments.mode == "preflight":
            return local_preflight(client, settings)
        summary = await login_smoke(client, settings, authorized=arguments.authorize_login)
        if arguments.mode == "login":
            return summary
        return await guild_sync(client, settings, authorized=arguments.authorize_guild_sync)


def _validate_mode_authorization(arguments: argparse.Namespace) -> None:
    """@description: Reject ambiguous or unauthorized modes before loading secrets or creating a client.
    @param arguments: Explicit acceptance CLI mode and switches.
    @return: None; unsupported combinations fail closed before login.
    """
    if arguments.mode not in {"preflight", "login", "guild-sync"}:
        raise AcceptanceError("Unsupported acceptance mode")
    if arguments.mode == "preflight" and (arguments.authorize_login or arguments.authorize_guild_sync):
        raise AcceptanceError("Preflight does not accept network authorization switches")
    if arguments.mode != "preflight" and arguments.authorize_login is not True:
        raise AcceptanceError("This operation requires explicit login authorization")
    if arguments.authorize_guild_sync and arguments.mode != "guild-sync":
        raise AcceptanceError("Guild sync authorization requires the dedicated guild-sync mode")
    if arguments.mode == "guild-sync" and arguments.authorize_guild_sync is not True:
        raise AcceptanceError("Guild sync requires separate explicit authorization")


def main() -> None:
    """@description: Sanitize command-boundary failures and prevent platform debug logs exposing secrets.
    @param: none
    @return: None; expected failures exit with a public category and no raw traceback.
    """
    arguments = argument_parser().parse_args()
    execute_acceptance(arguments, run_acceptance)


if __name__ == "__main__":
    main()
