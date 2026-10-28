"""Start the HTTP interaction endpoint as a separate entry point, bound to loopback and never connected to the Discord Gateway.

Settings are validated before the service is built; the reverse proxy on the same host is the only public ingress.
"""

import logging
from functools import partial

from aiohttp import web

from bot.config import ConfigurationError
from bot.database.connection import open_connection
from bot.database.repository import MusicRepository
from bot.database.submission_repository import SubmissionRepository
from bot.http_config import HttpInteractionSettings, load_http_interaction_settings
from bot.http_interactions import build_app
from bot.services import MusicService


def build_service(settings: HttpInteractionSettings) -> MusicService:
    """@description: Construct the existing application service exactly as the Gateway entry does, with the formal switch from settings.
    @param settings: Validated HTTP interaction settings.
    @return: Application service ready for dispatch.
    """
    return MusicService(MusicRepository(settings.database),
                        SubmissionRepository(partial(open_connection, settings.database), settings.owner_id),
                        formal_exchange=settings.formal_exchange)


def main() -> None:
    """@description: Validate configuration, then serve the endpoint on loopback until the process receives a stop signal.
    @param: none
    @return: None; configuration failures terminate before any socket opens.
    """
    logging.basicConfig(level=logging.INFO)
    try:
        settings = load_http_interaction_settings()
    except ConfigurationError as configuration_error:
        raise SystemExit(str(configuration_error)) from None
    web.run_app(build_app(build_service(settings), settings), host=settings.listen_host, port=settings.listen_port)


if __name__ == "__main__":
    main()
