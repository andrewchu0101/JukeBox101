"""Run acceptance CLIs through one credential-safe asynchronous process boundary.

Both REST preparation and gateway preparation reuse logging isolation and sanitized failure handling.
"""

import argparse
import asyncio
import json
import logging
from collections.abc import Awaitable, Callable

import discord
from aiohttp import ClientError

from bot.guild_acceptance import AcceptanceError


def execute_acceptance(arguments: argparse.Namespace, operation: Callable[[argparse.Namespace], Awaitable[dict]]) -> None:
    """@description: Prevent expected platform failures or SDK debug output from exposing credentials.
    @param arguments: Explicit CLI mode and authorization values.
    @param operation: Asynchronous acceptance operation owning client cleanup.
    @return: None; expected failures exit with safe categories and no raw exception content.
    """
    previous_log_disable_level = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        summary = asyncio.run(operation(arguments))
        print(json.dumps(summary, sort_keys=True))
    except (AcceptanceError, discord.DiscordException, ClientError, OSError, EOFError, ValueError, KeyError) as operation_error:
        raise SystemExit("Acceptance stopped: " + type(operation_error).__name__) from None
    except KeyboardInterrupt:
        raise SystemExit("Acceptance interrupted; inspect any in-flight external operation before retrying") from None
    finally:
        logging.disable(previous_log_disable_level)
