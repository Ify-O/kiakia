"""Use case: who is still waiting for a reply? (Problem #1: "I miss and forget to reply.")"""
from __future__ import annotations

from typing import List

from kiakia.domain.models import WaitingCustomer
from kiakia.domain.ports import MessageRepository


class ListWaitingCustomers:
    def __init__(self, messages: MessageRepository):
        self.messages = messages

    def run(self) -> List[WaitingCustomer]:
        return self.messages.waiting_customers()
