"""Use case: an overview of what's in the inbox."""

from kiakia.domain.models import InboxStats, Period
from kiakia.domain.ports import StatsQuery


class GetInboxStats:
    def __init__(self, stats: StatsQuery):
        self.stats = stats

    def run(self, period: Period | None = None) -> InboxStats:
        return self.stats.inbox_stats(period or Period())
