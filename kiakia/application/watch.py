"""Use case: keep importing new messages, live ("kiakia watch").

Each cycle runs ImportMessages with long polling, so it waits quietly until a message
arrives. If the channel or the database has a problem, it waits a little (5 s, 10 s,
20 s … up to 60 s) and tries again, instead of crashing. A bad token stops it, because
retrying can't fix that.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass

from kiakia.domain.errors import KiakiaError, SourceRejectedRequest

from .import_messages import ImportMessages

log = logging.getLogger(__name__)


@dataclass
class WatchStats:
    cycles: int = 0
    saved: int = 0
    errors: int = 0


class WatchMessages:
    def __init__(
        self,
        importer: ImportMessages,
        sleep: Callable[[float], None],
        base_backoff: float = 5.0,
        max_backoff: float = 60.0,
    ):
        self.importer = importer
        self.sleep = sleep
        self.base_backoff = base_backoff
        self.max_backoff = max_backoff

    def run(self, max_cycles: int | None = None, on_saved: Callable[[int], None] | None = None) -> WatchStats:
        stats = WatchStats()
        failures = 0
        while max_cycles is None or stats.cycles < max_cycles:
            stats.cycles += 1
            try:
                summary = self.importer.run(record_empty=False)
            except SourceRejectedRequest as exc:
                if "409" not in str(exc) and "Conflict" not in str(exc):
                    raise  # e.g. a wrong bot token: retrying won't help
                # 409 = another `import` is polling Telegram at the same time; wait and retry.
                log.warning("Another import is running at the same time; retrying shortly")
                failures += 1
                stats.errors += 1
                self.sleep(self.base_backoff)
                continue
            except KiakiaError as exc:
                failures += 1
                stats.errors += 1
                delay = min(self.max_backoff, self.base_backoff * 2 ** (failures - 1))
                log.warning("Import problem (%s); retrying in %.0f s", exc, delay)
                self.sleep(delay)
                continue
            failures = 0
            if summary.saved:
                stats.saved += summary.saved
                if on_saved:
                    on_saved(summary.saved)
        return stats
