"""Use case: an overview of what's in the inbox."""

from kiakia.domain.models import InboxStats
from kiakia.domain.ports import StatsQuery


class GetInboxStats:
    def __init__(self, stats: StatsQuery):
        self.stats = stats

    def run(self) -> InboxStats:
        return self.stats.inbox_stats()
