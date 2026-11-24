"""Use case: import new messages from a source into storage.

Depends only on PORTS (interfaces), never on Telegram or PostgreSQL, so it
works unchanged for every future channel and can be tested with fakes.

Order matters for reliability ("save first, confirm second"):
  1. fetch after the stored cursor
  2. save the messages (one transaction)
  3. only THEN store the new cursor
If we crash between 2 and 3, the next run re-fetches the same messages and
the idempotent save ignores the duplicates. Nothing is lost, nothing is doubled.
"""

import logging
from dataclasses import dataclass

from kiakia.domain.ports import CursorStore, ImportRunLog, MessageRepository, MessageSource

log = logging.getLogger(__name__)


@dataclass
class ImportSummary:
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
        cursors: CursorStore,
        runs: ImportRunLog,
        batch_size: int = 100,
    ):
        self.source = source
        self.messages = messages
        self.cursors = cursors
        self.runs = runs
        self.batch_size = batch_size

    def run(self, max_records: int | None = None, max_batches: int = 1000) -> ImportSummary:
        summary = ImportSummary()
        run_id = self.runs.start_run(self.source.name)
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

                summary.batches += 1
                summary.seen += len(result.messages) + result.ignored + len(result.rejects)
                summary.ignored += result.ignored
                summary.bad += len(result.rejects)
                for reject in result.rejects:
                    log.warning("Skipped bad record %s: %s", reject.ref, reject.reason)

                summary.saved += self.messages.save_messages(result.messages)  # 1. save
                self.cursors.set_cursor(self.source.name, result.next_cursor)  # 2. then move on
        except Exception as exc:
            # Recording the failure must never hide the real cause: if the database is
            # down, finish_run fails too, so log that and re-raise the ORIGINAL error.
            try:
                self.runs.finish_run(
                    run_id,
                    status="failed",
                    seen=summary.seen,
                    saved=summary.saved,
                    bad=summary.bad,
                    error=f"{type(exc).__name__}: {exc}"[:500],
                )
            except Exception as log_exc:
                log.error("Could not record failed run %s: %s", run_id, log_exc)
            raise
        self.runs.finish_run(
            run_id, status="succeeded", seen=summary.seen, saved=summary.saved, bad=summary.bad
        )
        return summary
