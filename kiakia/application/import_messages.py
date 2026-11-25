"""Use case: import new messages from a source into storage.

Depends only on PORTS (interfaces), never on Telegram or PostgreSQL, so it
works unchanged for every future channel and can be tested with fakes.

Order matters for reliability ("save first, confirm second"):
  1. fetch after the stored cursor
  2. save the good messages (one transaction) and keep the bad ones (dead letter)
  3. only THEN store the new cursor
If we crash before 3, the next run re-fetches the same messages and
the idempotent save ignores the duplicates. Nothing is lost, nothing is doubled.
"""

import logging
from dataclasses import dataclass

from kiakia.domain.models import RunStatus
from kiakia.domain.ports import (
    CursorStore,
    ImportRunLog,
    MessageRepository,
    MessageSource,
    OptOutRecorder,
    RejectStore,
)

from .customers import is_opt_out_message

log = logging.getLogger(__name__)


@dataclass
class ImportSummary:
    source: str = ""
    batches: int = 0
    seen: int = 0  # updates received (messages + ignored + rejected)
    saved: int = 0
    ignored: int = 0
    bad: int = 0


class ImportMessages:
    def __init__(
        self,
        source: MessageSource,
        messages: MessageRepository,
        rejects: RejectStore,
        cursors: CursorStore,
        runs: ImportRunLog,
        batch_size: int = 100,
        opt_outs: OptOutRecorder | None = None,
    ):
        self.source = source
        self.messages = messages
        self.rejects = rejects
        self.cursors = cursors
        self.runs = runs
        self.batch_size = batch_size
        self.opt_outs = opt_outs

    def run(
        self, max_records: int | None = None, max_batches: int = 1000, record_empty: bool = True
    ) -> ImportSummary:
        """`record_empty=False` (used by `watch`) only logs runs that actually received something,
        so quiet periods don't fill import_runs with thousands of empty rows."""
        summary = ImportSummary(source=self.source.name)
        run_id: int | None = self.runs.start_run(self.source.name) if record_empty else None
        try:
            while summary.batches < max_batches:
                limit = self.batch_size
                if max_records is not None:
                    limit = min(limit, max_records - summary.seen)
                    if limit <= 0:
                        break
                cursor = self.cursors.get_cursor(self.source.name)
                result = self.source.fetch(cursor, limit)
                if result.next_cursor is None:  # nothing new
                    break

                if run_id is None:
                    run_id = self.runs.start_run(self.source.name)
                summary.batches += 1
                summary.seen += len(result.messages) + result.ignored + len(result.rejects)
                summary.ignored += result.ignored
                summary.bad += len(result.rejects)
                for reject in result.rejects:
                    log.warning("Skipped bad record %s: %s", reject.ref, reject.reason)

                summary.saved += self.messages.save_messages(result.messages)  # 1. save good records
                self.rejects.save_rejects(self.source.name, result.rejects)  # 2. keep bad ones (W5)
                if self.opt_outs is not None:  # a customer replying STOP leaves the offers list (ADR 0007)
                    for m in result.messages:
                        if is_opt_out_message(m) and self.opt_outs.withdraw_marketing(m):
                            log.info("Customer opted out of offers (STOP) on %s", m.channel.value)
                self.cursors.set_cursor(self.source.name, result.next_cursor)  # 3. only then move on
        except Exception as exc:
            # Recording the failure must never hide the real cause: if the database is
            # down, finish_run fails too, so log that and re-raise the ORIGINAL error.
            try:
                if run_id is None:  # failures are always recorded, even in quiet mode
                    run_id = self.runs.start_run(self.source.name)
                self.runs.finish_run(
                    run_id,
                    status=RunStatus.FAILED,
                    seen=summary.seen,
                    saved=summary.saved,
                    bad=summary.bad,
                    error=f"{type(exc).__name__}: {exc}"[:500],
                )
            except Exception as log_exc:
                log.error("Could not record failed run %s: %s", run_id, log_exc)
            raise
        if run_id is not None:
            self.runs.finish_run(
                run_id, status=RunStatus.SUCCEEDED, seen=summary.seen, saved=summary.saved, bad=summary.bad
            )
        return summary
