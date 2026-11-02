"""Verify the batched official lookup used to compare a new submission with existing songs, with a scripted transport only.

The first transport argument is the comma-joined identifier list of one videos.list request (the API accepts up to 50 per call).
No test here reaches the network; quota, retry and cooldown behaviour is observed through the public result.
"""

import pytest

from bot.youtube_metadata import BATCH_LOOKUP_SIZE, BatchStatus, TransportFailure
from tests.test_youtube_metadata import ScriptedTransport, api_item, client_for


def ids(count: int, start: int = 0) -> list[str]:
    """@description: Build distinct eleven-character identifiers.
    @param count: How many.
    @param start: First index.
    @return: Identifier list.
    """
    return [f"vid{index:08d}" for index in range(start, start + count)]


def response_for(video_ids) -> dict:
    """@description: Build an official-looking response for the given identifiers.
    @param video_ids: Identifiers to include.
    @return: Response dictionary.
    """
    return {"items": [api_item(id=video_id) for video_id in video_ids]}


def test_the_batch_size_is_the_api_maximum() -> None:
    """@description: Fifty identifiers per request is the documented limit and costs one quota unit.
    @param: none
    @return: None.
    """
    assert BATCH_LOOKUP_SIZE == 50


def test_identifiers_are_requested_in_chunks_of_fifty() -> None:
    """@description: 130 identifiers need exactly three requests of 50, 50 and 30.
    @param: none
    @return: None.
    """
    requested = ids(130)
    transport = ScriptedTransport(*(response_for(requested[start:start + 50]) for start in (0, 50, 100)))
    result = client_for(transport).lookup_many(requested)
    assert [len(call[0].split(",")) for call in transport.calls] == [50, 50, 30]
    assert result.status is BatchStatus.COMPLETE and len(result.found) == 130


def test_exactly_fifty_identifiers_use_one_request() -> None:
    """@description: The chunk boundary must not create an empty second request.
    @param: none
    @return: None.
    """
    requested = ids(50)
    transport = ScriptedTransport(response_for(requested))
    assert client_for(transport).lookup_many(requested).status is BatchStatus.COMPLETE
    assert len(transport.calls) == 1


def test_an_empty_list_makes_no_request() -> None:
    """@description: Nothing to compare costs no quota.
    @param: none
    @return: None.
    """
    transport = ScriptedTransport()
    result = client_for(transport).lookup_many([])
    assert (result.status, result.found, transport.calls) == (BatchStatus.COMPLETE, (), [])


def test_duplicate_requested_identifiers_are_asked_once() -> None:
    """@description: A repeated identifier must not waste quota or make the response look malformed.
    @param: none
    @return: None.
    """
    transport = ScriptedTransport(response_for(ids(2)))
    result = client_for(transport).lookup_many(ids(2) + ids(2))
    assert transport.calls[0][0].split(",") == ids(2) and result.status is BatchStatus.COMPLETE


def test_deleted_or_private_old_videos_are_skipped_not_failures() -> None:
    """@description: An old video missing from the response is simply not comparable; the comparison is still complete.
    @param: none
    @return: None.
    """
    requested = ids(5)
    result = client_for(ScriptedTransport(response_for(requested[:3]))).lookup_many(requested)
    assert result.status is BatchStatus.COMPLETE and [item.video_id for item in result.found] == requested[:3]


@pytest.mark.parametrize("response", [
    {"items": [api_item(id="vid00000000"), api_item(id="vid00000000")]},
    {"items": [api_item(id="notrequested")]},
    {"items": "nope"}, {"unexpected": []}, ["not", "a", "dict"]])
def test_an_untrustworthy_response_makes_the_comparison_incomplete(response) -> None:
    """@description: Duplicate, unrequested or malformed items must never be treated as a clean comparison.
    @param response: Malformed or surprising response.
    @return: None.
    """
    result = client_for(ScriptedTransport(response)).lookup_many(["vid00000000"])
    assert result.status is BatchStatus.INCOMPLETE and result.found == ()


def test_quota_exhaustion_stops_further_chunks_and_starts_the_cooldown() -> None:
    """@description: After quotaExceeded no more requests go out, now or on the next call during the cooldown.
    @param: none
    @return: None.
    """
    requested = ids(120)
    transport = ScriptedTransport(response_for(requested[:50]), TransportFailure(403, "quotaExceeded"))
    client = client_for(transport)
    assert client.lookup_many(requested).status is BatchStatus.QUOTA_EXHAUSTED
    assert len(transport.calls) == 2
    assert client.lookup_many(ids(3)).status is BatchStatus.QUOTA_EXHAUSTED and len(transport.calls) == 2


def test_a_temporary_failure_retries_a_bounded_number_of_times_then_is_incomplete() -> None:
    """@description: Transient errors use the same bounded retry as a single lookup.
    @param: none
    @return: None.
    """
    transport = ScriptedTransport(TransportFailure(503, ""), TransportFailure(503, ""), TransportFailure(503, ""))
    result = client_for(transport).lookup_many(ids(3))
    assert result.status is BatchStatus.INCOMPLETE and len(transport.calls) == 3


def test_a_failure_in_any_chunk_makes_the_batch_incomplete() -> None:
    """@description: If any chunk fails, the batch is incomplete even though earlier chunks succeeded.
    @param: none
    @return: None.
    """
    requested = ids(60)
    transport = ScriptedTransport(response_for(requested[:50]), TransportFailure(400, "badRequest"))
    assert client_for(transport).lookup_many(requested).status is BatchStatus.INCOMPLETE


def test_the_key_goes_to_the_transport_only_and_never_appears_in_results() -> None:
    """@description: The credential must not leak into the result representation.
    @param: none
    @return: None.
    """
    transport = ScriptedTransport(response_for(ids(2)))
    result = client_for(transport).lookup_many(ids(2))
    assert transport.calls[0][1] == "fake-key" and "fake-key" not in repr(result)
