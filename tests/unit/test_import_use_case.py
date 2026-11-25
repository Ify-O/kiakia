"""Unit tests for the ImportMessages use case, using FAKE ports.

No Telegram, no database: this is what decoupling buys us.
"""

from datetime import UTC, datetime

import pytest

from kiakia.application.import_messages import ImportMessages
from kiakia.domain.errors import SourceUnavailable
from kiakia.domain.models import (
    Channel,
    Direction,
    FetchResult,
    Message,
    MessageKind,
    Reject,
    RunStatus,
    Sender,
)


def message(i):
    return Message(
        Channel.TELEGRAM,
        "1",
        str(i),
        Direction.IN,
        MessageKind.TEXT,
        datetime(2026, 1, 1, tzinfo=UTC),
        Sender("1", "Ada"),
        f"m{i}",
    )


class FakeSource:
    name = "fake"

    def __init__(self, batches):
        self.batches = list(batches)
        self.cursors_seen = []

    def fetch(self, cursor, limit):
        self.cursors_seen.append(cursor)
        return self.batches.pop(0) if self.batches else FetchResult()


class FakeStore:
    def __init__(self, fail_on_save=False):
        self.saved, self.cursor, self.runs, self.fail_on_save = [], None, [], fail_on_save
        self.rejected = []
        self.log = []  # order of calls, to prove 'save first, move cursor last'

    def save_messages(self, messages):
        if self.fail_on_save:
            raise RuntimeError("database down")
        self.log.append("save_messages")
        self.saved.extend(messages)
        return len(messages)

    def waiting_customers(self):
        return []

    def get_cursor(self, source):
        return self.cursor

    def save_rejects(self, source, rejects):
        self.log.append("save_rejects")
        self.rejected.extend(rejects)
        return len(rejects)

    def set_cursor(self, source, cursor):
        self.log.append("set_cursor")
        self.cursor = cursor

    def start_run(self, source):
        self.runs.append({"source": source})
        return len(self.runs)

    def finish_run(self, run_id, **kw):
        self.runs[run_id - 1].update(kw)


def use_case(source, store):
    return ImportMessages(source, messages=store, rejects=store, cursors=store, runs=store)


def test_imports_all_batches_and_moves_cursor():
    source = FakeSource(
        [
            FetchResult(messages=[message(1), message(2)], next_cursor="3"),
            FetchResult(messages=[message(3)], rejects=[Reject("bad", "x")], ignored=1, next_cursor="6"),
        ]
    )
    store = FakeStore()
    s = use_case(source, store).run()
    assert (s.saved, s.bad, s.ignored, s.seen, s.batches) == (3, 1, 1, 5, 2)
    assert source.cursors_seen == [None, "3", "6"]  # continues where it stopped
    assert store.cursor == "6"
    assert store.runs[0]["status"] is RunStatus.SUCCEEDED


def test_cursor_is_not_moved_when_saving_fails():
    source = FakeSource([FetchResult(messages=[message(1)], next_cursor="2")])
    store = FakeStore(fail_on_save=True)
    with pytest.raises(RuntimeError):
        use_case(source, store).run()
    assert store.cursor is None  # messages will be fetched again next time
    assert store.runs[0]["status"] is RunStatus.FAILED


def test_source_failure_is_logged_as_failed_run():
    class DownSource(FakeSource):
        def fetch(self, cursor, limit):
            raise SourceUnavailable("telegram getUpdates: timed out after 5 attempts")

    store = FakeStore()
    with pytest.raises(SourceUnavailable):
        use_case(DownSource([]), store).run()
    assert store.runs[0]["status"] is RunStatus.FAILED
    assert "timed out" in store.runs[0]["error"]


def test_original_error_survives_when_logging_the_failure_also_fails():
    # W1: DB down -> save fails AND finish_run fails. The user must see the first error.
    from kiakia.domain.errors import StorageUnavailable

    class DownStore(FakeStore):
        def save_messages(self, messages):
            raise StorageUnavailable("ORIGINAL: saving failed")

        def finish_run(self, run_id, **kw):
            raise StorageUnavailable("SECONDARY: could not log the run")

    source = FakeSource([FetchResult(messages=[message(1)], next_cursor="2")])
    with pytest.raises(StorageUnavailable, match="ORIGINAL"):
        use_case(source, DownStore()).run()


def test_limit_stops_after_n_records():
    source = FakeSource([FetchResult(messages=[message(i)], next_cursor=str(i + 1)) for i in range(10)])
    s = use_case(source, FakeStore()).run(max_records=3)
    assert s.saved == 3


def test_rejects_are_kept_before_the_cursor_moves():
    # W5: bad records go to the dead-letter store, and the cursor moves only after that.
    bad = Reject("missing message.date", "7", raw={"update_id": 7})
    source = FakeSource([FetchResult(messages=[message(1)], rejects=[bad], next_cursor="8")])
    store = FakeStore()
    use_case(source, store).run()
    assert store.rejected == [bad]
    assert store.log == ["save_messages", "save_rejects", "set_cursor"]


def test_quiet_mode_does_not_log_empty_runs_but_logs_real_ones():
    empty = FakeStore()
    use_case(FakeSource([]), empty).run(record_empty=False)
    assert empty.runs == []  # nothing arrived: no import_runs row
    busy = FakeStore()
    use_case(FakeSource([FetchResult(messages=[message(1)], next_cursor="2")]), busy).run(record_empty=False)
    assert len(busy.runs) == 1 and busy.runs[0]["status"] is RunStatus.SUCCEEDED
