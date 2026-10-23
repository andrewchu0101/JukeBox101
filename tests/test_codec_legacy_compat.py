"""Verify persisted review contracts written before the reexamination fields still decode safely.

Pure codec test; no database, no MySQL, no Discord.
"""

import json

from bot.database.submission_codec import decode_contract, encode_contract
from bot.eligibility import DurationEvidence
from bot.review import ReviewSnapshot, ReviewStatus
from bot.song_identity import VideoIdentity


def test_legacy_review_payload_without_new_fields_decodes_with_safe_defaults():
    """@description: Old stored reviews lack reexamination and priority fields; they must decode as not-pending, not-released.
    @param: none
    @return: None.
    """
    snapshot = ReviewSnapshot(VideoIdentity("Synth0001A1"), 2, ReviewStatus.REJECTED)
    document = json.loads(encode_contract(snapshot))
    fields = document["value"]["fields"]
    fields.pop("reexamination_pending", None)
    fields.pop("priority_released", None)
    decoded = decode_contract(json.dumps(document), ReviewSnapshot)
    assert decoded.status is ReviewStatus.REJECTED
    assert decoded.reexamination_pending is False
    assert decoded.priority_released is False


def test_new_review_payload_round_trips_both_fields():
    """@description: Newly written reviews keep the reexamination marker and priority release across storage.
    @param: none
    @return: None.
    """
    video = VideoIdentity("Synth0001A1")
    duration = DurationEvidence(video, 180, "synthetic-duration", True, True)
    snapshot = ReviewSnapshot(video, 3, ReviewStatus.PENDING_REVIEW, duration=duration,
                              reexamination_pending=True, priority_released=True)
    assert decode_contract(encode_contract(snapshot), ReviewSnapshot) == snapshot
