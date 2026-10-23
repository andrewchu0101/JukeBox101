"""Walk one formal reexamination through the real service, repository, fake database and reward evaluator.

Offline synthetic walkthrough for the Owner. It prints each step and the settlement result; nothing reaches MySQL or Discord.
Run: ./.venv/Scripts/python.exe -X utf8 -m tests.owner_review_walkthrough
"""

from tests.intake_fixture import seed_intake
from datetime import datetime, timedelta, timezone

from bot.database.submission_repository import SubmissionRepository
from bot.database.review_persistence import ReviewCommand
from bot.eligibility import DurationEvidence
from bot.evidence_record import EvidenceSourceType, VersionClaimKind, record_evidence
from bot.owner_review import OwnerReviewService, ReviewAction
from bot.song_identity import VideoIdentity
from bot.song_judgement import ContentJudgement
from bot.submission_workflow import SubmissionRequest
from bot.review import ReviewSnapshot
from bot.version_identity import RecordingVersionIdentity, VersionAssignment, VersionIdentityStatus
from tests.submission_persistence_fake import FakeDatabase

OWNER_ID = 777
START = datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc)


class Clock:
    """@description: Synthetic clock so token expiry is deterministic.
    @param: none
    @return: Callable clock returning a fixed instant.
    """

    def __call__(self) -> datetime:
        """@description: Return the synthetic instant.
        @param self: Clock.
        @return: Timezone-aware instant.
        """
        return START


def main() -> None:
    """@description: Run the reexamination flow step by step and print each observable result.
    @param: none
    @return: None.
    """
    database = FakeDatabase()
    database.now = START.replace(tzinfo=None)
    repository = SubmissionRepository(database.connect, OWNER_ID)
    service = OwnerReviewService(repository, OWNER_ID, Clock(), token_ttl=timedelta(minutes=5))
    video = VideoIdentity("Synth0001X1")

    # Step 1: intake with a valid duration; the review is pending and the version is still unknown.
    seed_intake(repository, SubmissionRequest(f"https://youtu.be/{video.video_id}", "op-1", 101, 999, VersionAssignment(video),
                                        ReviewSnapshot(video), 0,
                                        DurationEvidence(video, 180, "synthetic-duration", True, True)))
    print("1. intake: review =", service.view(OWNER_ID, "op-1").review.status.value)

    # Step 1b: the Owner approves the song with a content judgement and a citation (no award yet: version unknown).
    judgement = ContentJudgement(video, OWNER_ID, "synthetic song judgement", START, "demo-content-op-1", True)
    token = service.preview(OWNER_ID, "op-1", ReviewAction.APPROVE, "synthetic approval", 1, "demo-cite-1",
                            content=judgement).token_id
    service.confirm(OWNER_ID, token)
    print("1b. approved: review =", service.view(OWNER_ID, "op-1").review.status.value)

    # Step 2: an ordinary Owner review confirms version A (identity mapping through the existing path).
    basis = record_evidence("ev-a", video, EvidenceSourceType.OWNER_REVIEW, "synthetic-basis-note",
                            START, "synthetic basis inspection", OWNER_ID, RecordingVersionIdentity("synth-version-A"),
                            version_kind=VersionClaimKind.SAME_RECORDING_AUDIO_MV)
    service.add_reexamination_evidence(OWNER_ID, "op-1", basis, "synthetic basis inspection", "basis-1")
    revision = service.view(OWNER_ID, "op-1").review.version
    repository.review(ReviewCommand("seed-identity-a", "op-1", OWNER_ID, "synthetic confirmed mapping", revision,
                                    identity=VersionAssignment(video, VersionIdentityStatus.CONFIRMED,
                                                               RecordingVersionIdentity("synth-version-A"), "ev-a")))
    print("2. identity A confirmed:", service.view(OWNER_ID, "op-1").identity.status.value)

    # Step 3: formal reexamination with NEW owner-confirmed evidence contradicting version A.
    contradicting = record_evidence("ev-b", video, EvidenceSourceType.OWNER_REVIEW, "synthetic-reexam-note",
                                    START + timedelta(hours=1), "synthetic reexamination", OWNER_ID,
                                    RecordingVersionIdentity("synth-version-B"), version_kind=VersionClaimKind.COVER)
    revision = service.view(OWNER_ID, "op-1").review.version
    token = service.preview_reexamination(OWNER_ID, "op-1", contradicting, "synthetic reexamination", revision).token_id
    service.confirm(OWNER_ID, token)
    view = service.view(OWNER_ID, "op-1")
    print("3. formal reexamination: identity =", view.identity.status.value,
          "| marker =", view.review.reexamination_pending)

    # Step 4: the real reward path must block; no ledger row is created.
    print("4. settle:", repository.settle("op-1").status.value, "| ledger rows:", len(database.state["credit_ledger"]))

    # Step 5: a supplementary (補件) record with the same contradiction does NOT change identity or review.
    supplementary = record_evidence("ev-c", video, EvidenceSourceType.OWNER_REVIEW, "synthetic-supplement",
                                    START + timedelta(hours=2), "synthetic supplement", OWNER_ID,
                                    RecordingVersionIdentity("synth-version-C"))
    before = service.view(OWNER_ID, "op-1")
    service.add_reexamination_evidence(OWNER_ID, "op-1", supplementary, "synthetic supplement", "supplement-1")
    after = service.view(OWNER_ID, "op-1")
    print("5. supplement (補件): identity", before.identity.status.value, "->", after.identity.status.value,
          "| review", before.review.status.value, "->", after.review.status.value,
          "| marker", before.review.reexamination_pending, "->", after.review.reexamination_pending,
          "| evidence", len(before.evidence), "->", len(after.evidence))


if __name__ == "__main__":
    main()
