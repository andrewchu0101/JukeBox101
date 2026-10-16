"""Provide honest transport-only command outcomes for the development gateway listener.

These pure asynchronous operations have no repository, database settings, credits or scheduling collaborators.
"""

from bot.urls import parse_youtube_url

MODE_LABEL = "Development / DB-disabled"


class GatewayAcceptanceService:
    """Implement the existing handler-facing operations without accepting persistence dependencies."""

    async def share(self, discord_user_id: int, source_url: str) -> str:
        """@description: Verify URL transport while clearly refusing song persistence and credits.
        @param self: Transport-only acceptance service.
        @param discord_user_id: Requester identity, not stored or logged.
        @param source_url: User input validated locally without retrieving metadata.
        @return: Explicit test-mode outcome; invalid inputs raise the existing validation error.
        """
        parse_youtube_url(source_url)
        return f"{MODE_LABEL}：網址格式有效；沒有儲存歌曲或投稿，也沒有發放點數。"

    async def exchange(self, discord_user_id: int) -> str:
        """@description: Acknowledge the published exchange command without creating a recommendation.
        @param self: Transport-only acceptance service.
        @param discord_user_id: Requester identity, not stored or logged.
        @return: Honest disabled-exchange outcome without a debit.
        """
        return f"{MODE_LABEL}：交換功能尚未啟用；未扣點，也未建立推薦紀錄。"

    async def balance(self, discord_user_id: int) -> str:
        """@description: Avoid fabricating an account balance when persistence is disabled.
        @param self: Transport-only acceptance service.
        @param discord_user_id: Requester identity, not queried or stored.
        @return: Explicit inability to query credits, without displaying a fake zero.
        """
        return f"{MODE_LABEL}：資料庫停用，無法查詢餘額。"

    async def history(self, discord_user_id: int) -> str:
        """@description: Distinguish unavailable history from an actual empty recommendation history.
        @param self: Transport-only acceptance service.
        @param discord_user_id: Requester identity, not queried or stored.
        @return: Explicit inability to query history without claiming it is empty.
        """
        return f"{MODE_LABEL}：資料庫停用，無法查詢歷史紀錄。"

    async def daily(self, discord_user_id: int, enabled: bool) -> str:
        """@description: Acknowledge daily choice transport while refusing persistent preferences or jobs.
        @param self: Transport-only acceptance service.
        @param discord_user_id: Requester identity, not stored or logged.
        @param enabled: Received preference choice; deliberately never persisted.
        @return: Explicit unsaved-setting outcome with no scheduler or DM.
        """
        preference = "on" if enabled else "off"
        return f"{MODE_LABEL}：收到 {preference}，但設定未保存；不啟動排程，也不發送 DM。"
