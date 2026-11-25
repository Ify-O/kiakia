"""Customer rights and the consented export (docs/adr/0009).

- ForgetCustomer: the right to erasure. The store deletes everything in one transaction and
  logs that an erasure happened, without saying who.
- ExportCustomers: the offers list (marketing AND details consent) as a spreadsheet file.
  Which columns, how phones look and how cells are made safe is decided here (pure functions,
  unit-tested); the file format itself (CSV, Excel) is an adapter.
"""

import re
from collections.abc import Mapping
from datetime import UTC, datetime

from kiakia.domain.errors import NotFound
from kiakia.domain.models import (
    ErasureSummary,
    ExportFile,
    ExportFormat,
    ExportLayout,
    MarketingContact,
    Table,
)
from kiakia.domain.ports import CustomerEraser, MarketingListQuery, PrivacyLog, SpreadsheetWriter

PLATFORM_LABEL = {
    "telegram": "Telegram",
    "whatsapp": "WhatsApp",
    "instagram": "Instagram",
    "messenger": "Messenger",
    "tiktok": "TikTok",
    "email": "Email",
}

# Excel/Sheets treat a cell starting with these as a formula (OWASP "CSV injection").
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _plain(value: str | None) -> str:
    return value or ""


def safe_cell(value: str | None) -> str:
    """Text a spreadsheet will show, never run: `=HYPERLINK(...)` becomes `'=HYPERLINK(...)`."""
    if not value:
        return ""
    return "'" + value if value.startswith(_FORMULA_START) else value


def international_phone(phone: str | None, default_country_code: str = "234") -> str:
    """`0802 555 0123` / `+234 (802) 555-0123` -> `+2348025550123` (the E.164 form Google Ads needs).
    Numbers starting with 0 are taken as Nigerian; anything else needs its country code already."""
    if not phone:
        return ""
    digits = re.sub(r"\D", "", phone)
    if not digits:
        return ""
    if phone.strip().startswith("+") or digits.startswith(default_country_code):
        return "+" + digits
    if digits.startswith("0"):
        return "+" + default_country_code + digits[1:]
    return "+" + digits


def build_table(contacts: list[MarketingContact], layout: ExportLayout, *, escape: bool = True) -> Table:
    """`escape=False` only for formats that store every cell as text (Excel), where a `'`
    would just show up as a stray character."""
    cell = safe_cell if escape else _plain
    if layout is ExportLayout.GOOGLE_ADS:
        rows = []
        for c in contacts:
            phone = international_phone(c.phone)
            first, _, last = (c.full_name or "").strip().partition(" ")  # only a name THEY gave us
            rows.append(
                [
                    cell((c.email or "").strip().lower()),
                    phone,  # digits and a leading + only: already safe, and Google needs the +
                    cell(first),
                    cell(last.strip()),
                    "NG" if phone.startswith("+234") else "",
                ]
            )
        return Table(["Email", "Phone", "First Name", "Last Name", "Country"], rows)
    return Table(
        ["Name", "Phone", "Email", "Platform", "Customer since"],
        [
            [
                cell(c.full_name or c.display_name),
                cell(c.phone),
                cell(c.email),
                PLATFORM_LABEL.get(c.first_channel.value, c.first_channel.value),
                c.first_contact_at.date().isoformat(),
            ]
            for c in contacts
        ],
    )


class ForgetCustomer:
    def __init__(self, store: CustomerEraser):
        self.store = store

    def run(self, customer_id: int) -> ErasureSummary:
        summary = self.store.forget_customer(customer_id)
        if summary is None:
            raise NotFound(f"Customer {customer_id} does not exist.")
        return summary


class ExportCustomers:
    def __init__(
        self,
        customers: MarketingListQuery,
        log: PrivacyLog,
        writers: Mapping[ExportFormat, SpreadsheetWriter],
    ):
        self.customers = customers
        self.log = log
        self.writers = writers

    def run(
        self, export_format: ExportFormat, layout: ExportLayout, today: datetime | None = None
    ) -> ExportFile:
        writer = self.writers[export_format]
        contacts = self.customers.marketing_list()
        # Excel cells are written as text, so only CSV needs the formula guard.
        content = writer.write(build_table(contacts, layout, escape=export_format is ExportFormat.CSV))
        self.log.log_export(export_format, layout, len(contacts))
        day = (today or datetime.now(UTC)).date().isoformat()
        suffix = "-google-ads" if layout is ExportLayout.GOOGLE_ADS else ""
        return ExportFile(
            filename=f"kiakia-offers-list-{day}{suffix}.{writer.extension}",
            media_type=writer.media_type,
            content=content,
            row_count=len(contacts),
        )
