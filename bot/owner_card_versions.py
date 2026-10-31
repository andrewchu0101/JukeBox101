"""Build the text, links and evidence of the case card's version actions, without any service call or Discord object.

The card shows known versions by their first video, never by an internal id, and records the Owner's own comparison as a manual-check source.
Nothing here claims a recording category: category evidence keeps its existing path through /owner confirm_version.
"""

import uuid
from datetime import datetime

from bot.evidence_record import EvidenceRecord, EvidenceSourceType, record_evidence
from bot.song_identity import VideoIdentity
from bot.version_identity import RecordingVersionIdentity

WATCH_URL_PREFIX = "https://www.youtube.com/watch?v="  # [recognizable link]: Built from the video id, so tracking parameters never reach a source reference.
NEW_VERSION_PREFIX = "rv-"  # [opaque id]: Followed by random hex; never derived from a video id or a link.
KNOWN_VERSIONS_PER_CARD = 5  # [card size]: One row of one-press buttons per page.
NEW_REASON = "Owner 於案件卡片確認為新的錄音版本"  # [audit reason]: Stored with a version created from a card.
SAME_REASON = "Owner 於案件卡片確認與既有版本為同一錄音"  # [audit reason]: Stored with a mapping made from a card.
NEW_HEADLINE = "已記錄：此影片為一個獨立的錄音版本（由 Owner 判斷）。"
SAME_HEADLINE = "已記錄：此影片與所選版本為同一錄音（由 Owner 判斷，未宣稱特定類別）。"
OTHER_HINT = ("其他情況（Live、Cover、Remix、重新錄製、重製等）需要類別與可追溯的來源，請用 /owner confirm_version"
              "（version_kind 與 source 參數）；證據要求不變。這個按鈕不寫入任何資料。")
PROMISE_LINES = (
    "• 「確認為新的錄音版本」表示由你判斷：這支影片是一個獨立的錄音版本。來源記為「人工核對：此影片連結」。",
    "• 「確認為同一錄音」表示你已比對兩支影片，判斷是同一個錄音；不宣稱特定類別（MV、純音源、重製等），來源記為兩支影片連結。",
    "• 是否發獎依既有規則：先投稿者優先、同一版本只獎勵一次，且只有自動發獎開關開啟、其餘資格成立時才結算。結果會在按下後顯示。",
)  # [commitment]: What a press promises, shown before the press because the card is the preview.


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
                     actor_id: int) -> EvidenceRecord:
    """@description: Build the Owner's evidence record for a basic version assertion, with no category claimed.
    @param video: Video the evidence describes.
    @param identity: Asserted version.
    @param source: Manual-check source reference.
    @param retrieved_at: Time of the Owner's check.
    @param reason: Audit explanation.
    @param actor_id: Owner identifier.
    @return: Evidence record; the service marks it Owner-confirmed when previewing.
    """
    return record_evidence(uuid.uuid4().hex, video, EvidenceSourceType.OWNER_REVIEW, source, retrieved_at, reason, actor_id, identity)
