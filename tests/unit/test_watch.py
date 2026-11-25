"""Unit tests for the live watcher: keeps going after temporary problems, stops on a bad token."""

import pytest

from kiakia.application.import_messages import ImportSummary
from kiakia.application.watch import WatchMessages
from kiakia.domain.errors import SourceRejectedRequest, SourceUnavailable, StorageUnavailable


class FakeImporter:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def run(self, record_empty=True):
        self.calls.append(record_empty)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return ImportSummary(source="telegram", saved=outcome)


def test_keeps_watching_through_temporary_problems_with_growing_waits():
    importer = FakeImporter(
        [SourceUnavailable("timed out"), StorageUnavailable("db down"), SourceUnavailable("again"), 2, 0]
    )
    sleeps, saved = [], []
    stats = WatchMessages(importer, sleep=sleeps.append).run(max_cycles=5, on_saved=saved.append)
    assert sleeps == [5.0, 10.0, 20.0]  # backoff grows, then resets after a success
    assert saved == [2] and stats.saved == 2 and stats.errors == 3
    assert importer.calls == [False] * 5  # quiet mode: empty runs aren't logged


def test_a_bad_token_stops_the_watcher():
    importer = FakeImporter([SourceRejectedRequest("telegram getUpdates: Unauthorized")])
    with pytest.raises(SourceRejectedRequest):
        WatchMessages(importer, sleep=lambda s: None).run(max_cycles=3)


def test_a_parallel_import_conflict_is_waited_out():
    importer = FakeImporter(
        [SourceRejectedRequest("telegram getUpdates: HTTP 409 (check the token/request)"), 1]
    )
    sleeps = []
    stats = WatchMessages(importer, sleep=sleeps.append).run(max_cycles=2)
    assert sleeps == [5.0] and stats.saved == 1


def test_backoff_is_capped():
    importer = FakeImporter([SourceUnavailable("x")] * 6)
    sleeps = []
    WatchMessages(importer, sleep=sleeps.append, max_backoff=30).run(max_cycles=6)
    assert sleeps[-1] == 30
