"""CSV and Excel writers. They only turn a Table into bytes: which rows and columns, and making
cells safe, is decided by the use case (kiakia/application/privacy.py)."""

import csv
import io

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from kiakia.domain.models import Table


class CsvWriter:
    media_type = "text/csv; charset=utf-8"
    extension = "csv"

    def write(self, table: Table) -> bytes:
        buffer = io.StringIO()
        out = csv.writer(buffer, lineterminator="\r\n")  # RFC 4180 line endings
        out.writerow(table.headers)
        out.writerows(table.rows)
        # The BOM makes Excel read UTF-8 (₦, accents, emoji names) correctly; Google accepts it too.
        return buffer.getvalue().encode("utf-8-sig")


class XlsxWriter:
    media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    extension = "xlsx"

    def write(self, table: Table) -> bytes:
        book = Workbook()
        sheet = book.active
        if sheet is None:  # pragma: no cover - a new Workbook always has one sheet
            raise RuntimeError("openpyxl created a workbook without a sheet")
        sheet.title = "Offers list"
        sheet.append(table.headers)
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        for row in table.rows:
            sheet.append(row)
            for cell in sheet[sheet.max_row]:
                cell.data_type = "s"  # always text: phones keep their + and leading 0, nothing is a formula
        for index, header in enumerate(table.headers, start=1):
            width = max([len(header), *(len(row[index - 1]) for row in table.rows)])
            sheet.column_dimensions[get_column_letter(index)].width = min(width + 2, 50)
        sheet.freeze_panes = "A2"
        buffer = io.BytesIO()
        book.save(buffer)
        return buffer.getvalue()
