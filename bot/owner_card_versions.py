"""Build the text, links and evidence of the case card's version actions, without any service call or Discord object.

The card shows known versions by their first video, never by an internal id, and records the Owner's own comparison as a manual-check source.
Nothing here claims a recording category: category evidence keeps its existing path through /owner confirm_version.
"""

import uuid
from datetime import datetime

from bot.evidence_record import EvidenceRecord, EvidenceSourceType, VersionClaimKind, record_evidence
from bot.song_identity import VideoIdentity
from bot.version_identity import RecordingVersionIdentity

WATCH_URL_PREFIX = "https://www.youtube.com/watch?v="  # [recognizable link]: Built from the video id, so tracking parameters never reach a source reference.
NEW_VERSION_PREFIX = "rv-"  # [opaque id]: Followed by random hex; never derived from a video id or a link.
KNOWN_VERSIONS_PER_CARD = 5  # [card size]: One row of one-press buttons per page.
NEW_REASON = "Owner 於案件卡片確認為新的錄音版本"  # [audit reason]: Stored with a version created from a card.
SAME_REASON = "Owner 於案件卡片確認與既有版本為同一錄音"  # [audit reason]: Stored with a mapping made from a card.
NEW_HEADLINE = "已記錄：此影片為一個獨立的錄音版本（由 Owner 判斷）。"
SAME_HEADLINE = "已記錄：此影片與所選版本為同一錄音（由 Owner 判斷，未宣稱特定類別）。"
CATEGORY_BY_ORDINAL = {1: (VersionClaimKind.LIVE, "Live 現場"), 2: (VersionClaimKind.COVER, "Cover 翻唱"),
                       3: (VersionClaimKind.REMIX, "Remix"), 4: (VersionClaimKind.RE_RECORDING, "重新錄製")}  # [distinct categories]: Button ordinal to the existing category and its label.
REMASTER_LABEL = "重製（同一原始演出）"  # [same performance]: Maps to an existing version, so it is chosen from the known versions.
REMASTER_REASON = "Owner 於案件卡片確認為既有版本的重製（同一原始演出）"  # [audit reason]: Stored with a remaster mapping made from a card.
NO_VERSION_ERROR = "establishes no recording version"  # [service refusal]: Text of the existing rule's refusal, matched to keep the case pending.
OTHER_MENU_TEXT = ("其他情況：請選擇這支影片屬於哪一種。\n"
                   "• Live、Cover、Remix、重新錄製：為這支影片建立一個新的版本，並記錄你選的類別。\n"
                   "• 重製（同一原始演出）：對應到下列某個已知版本，視為同一錄音。\n"
                   "每個選項都會要求你輸入可追溯的判斷依據（連結或說明），必須由你輸入，系統不會自動帶入影片連結；網址本身不自動證明。\n"
                   "證據不足時不會建立任何版本，案件維持待處理。是否發獎依既有規則，結果按下後顯示。\n"
                   "也可以改用 /owner confirm_version（version_kind 與 source 參數），證據要求相同。")
SOURCE_REQUIRED_TEXT = ("判斷依據不符合既有規則（需要 8 到 512 個字元、可追溯的連結或說明，不能是純數字或互動編號）。"
                        "沒有建立任何版本，案件維持待處理。")
INSUFFICIENT_TEXT = "證據不足：依既有規則，這份證據不足以確認此影片的版本。沒有建立任何版本，案件維持待處理。"
PROMISE_LINES = (
    "• 「確認為新的錄音版本」表示由你判斷：這支影片是一個獨立的錄音版本。來源記為「人工核對：此影片連結」。",
    "• 「確認為同一錄音」表示你已比對兩支影片，判斷是同一個錄音；不宣稱特定類別（MV、純音源、重製等），來源記為兩支影片連結。",
    "• 是否發獎依既有規則：先投稿者優先、同一版本只獎勵一次，且只有自動發獎開關開啟、其餘資格成立時才結算。結果會在按下後顯示。",
)  # [commitment]: What a press promises, shown before the press because the card is the preview.


def retention_warning(status) -> str:
    """@description: Turn retention debt into the Owner's card warning, escalating with the days the purge has been overdue.
    @param status: Retention status from the database clock, or None when nothing is kept.
    @return: Warning lines, or an empty string while nothing is overdue by a day.
    """
    if status is None or status.oldest_overdue_days is None or status.oldest_overdue_days < 1:
        return ""
    days = int(status.oldest_overdue_days)
    head = f"⚠ API 資料清除已逾期 {days} 天（{status.overdue_rows} 筆）；這些資料不會被採信，但仍未清除。"
    if days >= 12:
        return f"{head}緊急：請停用 JUKEBOX_AUTO_REVIEW，並執行 python -m bot.api_retention purge --apply。\n"
    if days >= 7:
        return f"{head}緊急：請手動執行 python -m bot.api_retention purge --apply。\n"
    if days >= 3:
        return f"{head}已停止新的官方查詢，新案件改由 Owner 處理。\n"
    return f"{head}系統會自動重試。\n"


def watch_url(video_id: str) -> str:
    """@description: Build the plain watch link of a video from its id.
    @param video_id: Video id.
    @return: Watch URL without any parameter.
    """
    return f"{WATCH_URL_PREFIX}{video_id}"


def render_known_versions(versions, has_more: bool) -> str:
    """@description: List the known versions by their first video link and creation date, never by an internal id.
    @param versions: Known version rows of this page.
    @param has_more: Whether a further page exists.
    @return: Card block; the Owner compares the links with the submitted video.
    """
    if not versions:
        return "已知版本：無"
    rows = [f" {number}. 首位影片 {watch_url(version.first_video_id)}（{version.created_at.date().isoformat()} 建立）"
            for number, version in enumerate(versions, 1)]
    return "\n".join(["已知版本（點連結比對）：", *rows, *([" … 還有更多，按「更多已知版本」"] if has_more else [])])


def new_version_identity() -> RecordingVersionIdentity:
    """@description: Create the opaque identity of a version the Owner just confirmed as new.
    @param: none
    @return: Random version identity that reveals nothing about the video.
    """
    return RecordingVersionIdentity(f"{NEW_VERSION_PREFIX}{uuid.uuid4().hex}")


def manual_check_source(this_video_id: str, other_video_id: str | None = None) -> str:
    """@description: Record the Owner's own comparison as a traceable manual-check reference.
    @param this_video_id: Submitted video.
    @param other_video_id: First video of the selected known version, when the Owner mapped to one.
    @return: Source reference naming one or both links.
    """
    if other_video_id is None:
        return f"人工核對：{watch_url(this_video_id)}"
    return f"人工核對：{watch_url(this_video_id)} ↔ {watch_url(other_video_id)}"


def version_evidence(video: VideoIdentity, identity: RecordingVersionIdentity, source: str, retrieved_at: datetime, reason: str,
                     actor_id: int, version_kind: VersionClaimKind | None = None) -> EvidenceRecord:
    """@description: Build the Owner's evidence record for a basic version assertion, with no category claimed.
    @param video: Video the evidence describes.
    @param identity: Asserted version.
    @param source: Manual-check source reference.
    @param retrieved_at: Time of the Owner's check.
    @param reason: Audit explanation.
    @param actor_id: Owner identifier.
    @param version_kind: Existing version category the Owner chose, or None for a basic assertion.
    @return: Evidence record; the service marks it Owner-confirmed when previewing.
    """
    return record_evidence(uuid.uuid4().hex, video, EvidenceSourceType.OWNER_REVIEW, source, retrieved_at, reason, actor_id, identity,
                           version_kind=version_kind)


def category_reason(label: str) -> str:
    """@description: Name the category in the audit reason of a new version made from a card.
    @param label: Category label shown on the button.
    @return: Audit reason.
    """
    return f"Owner 於案件卡片確認為新的{label}版本"


def category_headline(label: str, remaster: bool) -> str:
    """@description: State what the Owner's judgement recorded, naming the category.
    @param label: Category label shown on the button.
    @param remaster: Whether the video was mapped to an existing version instead of creating one.
    @return: Sentence shown above the committed revision.
    """
    if remaster:
        return f"已記錄：此影片為所選版本的{label}（由 Owner 判斷，依你輸入的判斷依據）。"
    return f"已記錄：此影片為新的錄音版本，類別：{label}（由 Owner 判斷，依你輸入的判斷依據）。"
