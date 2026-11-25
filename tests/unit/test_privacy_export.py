"""Unit tests: the export rules (ADR 0009). Pure functions and fakes: no database."""

from datetime import UTC, datetime

import pytest

from kiakia.adapters.spreadsheet.writers import CsvWriter
from kiakia.application.privacy import (
    ExportCustomers,
    ForgetCustomer,
    build_table,
    international_phone,
    safe_cell,
)
from kiakia.domain.errors import NotFound
from kiakia.domain.models import Channel, ExportFormat, ExportLayout, MarketingContact

T0 = datetime(2026, 11, 25, 9, 0, tzinfo=UTC)


def contact(full_name="Ada Okafor", phone="0803 123 4567", email="Ada@Example.com"):
    return MarketingContact(full_name, "ada_tg", phone, email, Channel.TELEGRAM, T0)


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("0803 123 4567", "+2348031234567"),  # local Nigerian number
        ("+234 (803) 123-4567", "+2348031234567"),
        ("2348031234567", "+2348031234567"),
        ("+44 20 7946 0958", "+442079460958"),  # foreign numbers keep their own code
        ("", ""),
        (None, ""),
    ],
)
def test_international_phone(given, expected):
    assert international_phone(given) == expected


@pytest.mark.parametrize("dangerous", ["=SUM(A1)", "+1", "-2", "@x", "\tx", "\rx"])
def test_safe_cell_stops_formulas(dangerous):
    assert safe_cell(dangerous) == "'" + dangerous


def test_safe_cell_leaves_normal_text_alone():
    assert safe_cell("Ada Okafor") == "Ada Okafor" and safe_cell(None) == ""


def test_google_ads_layout_uses_only_the_name_they_gave_us():
    table = build_table([contact(), contact(full_name=None)], ExportLayout.GOOGLE_ADS)
    assert table.headers == ["Email", "Phone", "First Name", "Last Name", "Country"]
    assert table.rows[0] == ["ada@example.com", "+2348031234567", "Ada", "Okafor", "NG"]
    assert table.rows[1][2:4] == ["", ""]  # never the platform username


def test_standard_layout():
    [row] = build_table([contact()], ExportLayout.STANDARD).rows
    assert row == ["Ada Okafor", "0803 123 4567", "Ada@Example.com", "Telegram", "2026-11-25"]


class FakeStore:
    def __init__(self):
        self.logged = []

    def marketing_list(self):
        return [contact(phone="+234 803 123 4567")]

    def log_export(self, export_format, layout, rows):
        self.logged.append((export_format, layout, rows))

    def forget_customer(self, customer_id):
        return None


def test_export_is_logged_and_named():
    store = FakeStore()
    file = ExportCustomers(store, store, {ExportFormat.CSV: CsvWriter()}).run(
        ExportFormat.CSV, ExportLayout.GOOGLE_ADS, today=T0
    )
    assert file.filename == "kiakia-offers-list-2026-11-25-google-ads.csv"
    assert file.row_count == 1 and store.logged == [(ExportFormat.CSV, ExportLayout.GOOGLE_ADS, 1)]
    assert file.content.startswith(b"\xef\xbb\xbfEmail,Phone")  # UTF-8 BOM for Excel


def test_csv_escapes_but_excel_stores_text():
    phone_cell = build_table([contact(phone="+234 803")], ExportLayout.STANDARD).rows[0][1]
    assert phone_cell == "'+234 803"
    assert (
        build_table([contact(phone="+234 803")], ExportLayout.STANDARD, escape=False).rows[0][1] == "+234 803"
    )


def test_forgetting_an_unknown_customer_is_not_found():
    with pytest.raises(NotFound):
        ForgetCustomer(FakeStore()).run(99)
