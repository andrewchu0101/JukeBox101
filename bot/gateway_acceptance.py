"""Expose a default-offline, explicitly authorized development gateway acceptance CLI.

The existing client is always closed, and no database or synchronization option is available.
"""

import argparse

from bot.acceptance_config import load_development_guild_settings
from bot.acceptance_runtime import execute_acceptance
from bot.gateway_listener import build_gateway_client, gateway_preflight, listen_gateway
from bot.guild_acceptance import AcceptanceError


def argument_parser() -> argparse.ArgumentParser:
    """@description: Make gateway access a separate manual operation with no sync or persistence flags.
    @param: none
    @return: Parser defaulting to a network-free preflight.
    """
    parser = argparse.ArgumentParser(description="Guild-restricted Development / DB-disabled interaction acceptance")
    parser.add_argument("mode", choices=("preflight", "listen"), nargs="?", default="preflight")
    parser.add_argument("--authorize-gateway", action="store_true")
    return parser


def validate_gateway_mode(arguments: argparse.Namespace) -> None:
    """@description: Fail before reading secrets or creating a client for unauthorized listener requests.
    @param arguments: Explicit mode and gateway opt-in switch.
    @return: None.
    """
    if arguments.mode not in {"preflight", "listen"}:
        raise AcceptanceError("Unsupported gateway acceptance mode")
    if (arguments.mode == "listen") != (arguments.authorize_gateway is True):
        raise AcceptanceError("Only listen mode accepts and requires explicit gateway authorization")


async def run_gateway_acceptance(arguments: argparse.Namespace) -> dict:
    """@description: Own client cleanup for offline, successful, interrupted and failed gateway scenarios.
    @param arguments: Parsed explicit mode and authorization values.
    @return: Public summary with no credentials or database values.
    """
    validate_gateway_mode(arguments)
    settings = load_development_guild_settings()
    async with build_gateway_client(settings) as client:
        if arguments.mode == "preflight":
            return gateway_preflight(client, settings)
        return await listen_gateway(client, settings, authorized=arguments.authorize_gateway)


def main() -> None:
    """@description: Reuse the shared safe CLI boundary instead of introducing another bot runner.
    @param: none
    @return: None; expected failures expose only safe categories.
    """
    execute_acceptance(argument_parser().parse_args(), run_gateway_acceptance)


if __name__ == "__main__":
    main()
