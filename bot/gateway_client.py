"""Attach Gateway health only to the production client, leaving acceptance clients inert.

The pinned discord.py raw event is opt-in and only its heartbeat opcode is inspected.
"""

import asyncio
import json
import logging

from bot.discord_app import JukeBoxClient


LOGGER = logging.getLogger(__name__)
AUTO_SWEEP_INTERVAL_SECONDS = 300  # [resume cadence]: Unfinished lookups and settlements are resumed this often and once at startup.


class ProductionGatewayClient(JukeBoxClient):
    """Add process-owned health sampling without command sync or background delivery."""

    def __init__(self, service, **kwargs):
        """@description: Enable the pinned Discord debug event solely for heartbeat ACK evidence.
        @param self: Production client.
        @param service: Existing application service.
        @param kwargs: Existing command wiring passed unchanged.
        @return: None.
        """
        super().__init__(service, **kwargs)
        self._enable_debug_events = True  # [pinned adapter]: discord.py 2.7.1 gates raw receive dispatch here.
        self.gateway_health = None
        self._auto_sweep_task = None  # [automatic resume]: Created only when the service has a pipeline.

    async def setup_hook(self) -> None:
        """@description: Start health sampling on the Gateway's own event loop before connecting.
        @param self: Client owned by the main process.
        @return: None.
        """
        if self.gateway_health is not None:
            self.gateway_health.task = asyncio.create_task(self.gateway_health.monitor(self), name='gateway-health')
        pipeline = getattr(self.handlers.service, 'auto_pipeline', None)
        if pipeline is not None:
            try:
                await asyncio.to_thread(pipeline.maintain_retention)  # [purge first]: Expired API values are cleared before this process connects or looks anything up.
            except Exception as error:  # [startup boundary]: A failed purge never blocks startup; reads already refuse expired values and the sweep retries.
                LOGGER.error('Startup retention maintenance failed: %s', type(error).__name__)
            self._auto_sweep_task = asyncio.create_task(self._auto_sweep(pipeline), name='auto-review-sweep')

    async def _auto_sweep(self, pipeline) -> None:
        """@description: Resume unfinished automatic work at startup and periodically, off the event loop; one failed round never ends the loop.
        @param self: Client owned by the main process.
        @param pipeline: Automatic pipeline.
        @return: None; runs until cancelled at close.
        """
        while True:
            try:
                await asyncio.to_thread(pipeline.sweep)
            except Exception as error:  # [scheduled-job boundary]: Class name only; the next round retries from durable state.
                LOGGER.error('Automatic sweep round failed: %s', type(error).__name__)
            await asyncio.sleep(AUTO_SWEEP_INTERVAL_SECONDS)

    async def on_ready(self) -> None:
        """@description: Record READY only for the configured application identity.
        @param self: Connected client.
        @return: None.
        """
        if self.gateway_health is not None:
            matched = self.application_id == self.gateway_health.application_id
            self.gateway_health.mark('ready' if matched else 'identity_mismatch')

    async def on_resumed(self) -> None:
        """@description: Revalidate application identity after a resumed session.
        @param self: Reconnected client.
        @return: None.
        """
        await self.on_ready()

    async def on_disconnect(self) -> None:
        """@description: Invalidate health immediately when Discord disconnects.
        @param self: Client losing its Gateway connection.
        @return: None.
        """
        if self.gateway_health is not None:
            self.gateway_health.mark('disconnected')

    async def on_socket_raw_receive(self, payload: str | bytes) -> None:
        """@description: Count Gateway heartbeat ACKs without retaining any payload content.
        @param self: Client receiving raw debug events from pinned discord.py.
        @param payload: Decompressed JSON frame from discord.py 2.7.1; never retained or logged.
        @return: None.
        """
        if self.gateway_health is not None and json.loads(payload).get('op') == 11:
            self.gateway_health.acknowledge()

    async def close(self) -> None:
        """@description: Invalidate evidence and cancel its writer before closing Discord.
        @param self: Client whose shutdown is bounded by the main entry.
        @return: None.
        """
        try:
            if self.gateway_health is not None:
                self.gateway_health.mark('stopping')
        finally:
            await self._stop_health_writer()
            if self._auto_sweep_task is not None:
                self._auto_sweep_task.cancel()
            await super().close()

    async def _stop_health_writer(self) -> None:
        """@description: Settle the owned sampler without blocking Discord cleanup on stale evidence.
        @param self: Closing production client.
        @return: None.
        """
        if self.gateway_health is not None and self.gateway_health.task is not None:
            self.gateway_health.task.cancel()
            await asyncio.gather(self.gateway_health.task, return_exceptions=True)
