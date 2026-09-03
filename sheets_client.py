import gspread
from gspread.utils import rowcol_to_a1
from google.oauth2.service_account import Credentials
from config import GOOGLE_SERVICE_ACCOUNT_FILE

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


class SheetsClient:
    def __init__(self):
        if not GOOGLE_SERVICE_ACCOUNT_FILE:
            raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_FILE не задано в .env")
        creds = Credentials.from_service_account_file(GOOGLE_SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        self.gc = gspread.authorize(creds)

    def list_tabs(self, sheet_url: str) -> list:
        sh = self.gc.open_by_url(sheet_url)
        return [ws.title for ws in sh.worksheets()]

    def _rows_to_values(self, header: list, rows: list) -> list:
        """rows: list of dict(case, description, steps: list[str], priority)."""
        col_index = {}
        for name in ("case", "description", "steps", "priority"):
            for i, col_name in enumerate(header):
                if col_name.strip().lower() == name:
                    col_index[name] = i
                    break

        width = len(header)
        values = []
        for row in rows:
            values_row = [""] * width
            if "case" in col_index:
                values_row[col_index["case"]] = row["case"]
            if "description" in col_index:
                values_row[col_index["description"]] = row["description"]
            if "steps" in col_index:
                values_row[col_index["steps"]] = self._format_steps(row["steps"])
            if "priority" in col_index:
                values_row[col_index["priority"]] = row["priority"]
            values.append(values_row)
        return values

    @staticmethod
    def _format_steps(steps: list) -> str:
        """Join steps with a real newline, inserting a blank line before the
        expected-result ("ОР:") line — matches the existing sheet convention."""
        lines = []
        for line in steps:
            if line.startswith("ОР:") and lines and lines[-1] != "":
                lines.append("")
            lines.append(line)
        return "\n".join(lines)

    def append_test_cases(self, sheet_url: str, tab_name: str, rows: list) -> int:
        """Append rows after the last populated row (computed ourselves — gspread's
        append_rows table-detection has been observed to miscompute the boundary and
        silently overwrite existing rows). Returns rows appended."""
        sh = self.gc.open_by_url(sheet_url)
        ws = sh.worksheet(tab_name)
        all_values = ws.get_all_values()
        last_row = 0
        for i, r in enumerate(all_values, start=1):
            if any(c.strip() for c in r):
                last_row = i
        return self._write_rows(ws, all_values[0] if all_values else [], last_row + 1, rows)

    def replace_rows(self, sheet_url: str, tab_name: str, start_row: int, rows: list) -> int:
        """Overwrite existing rows starting at start_row (1-indexed sheet row). Returns rows written."""
        sh = self.gc.open_by_url(sheet_url)
        ws = sh.worksheet(tab_name)
        header = ws.get_all_values()[0] if ws.row_count else []
        return self._write_rows(ws, header, start_row, rows)

    def _write_rows(self, ws, header: list, start_row: int, rows: list) -> int:
        values = self._rows_to_values(header, rows)
        width = len(header)
        end_row = start_row + len(values) - 1
        range_name = f"{rowcol_to_a1(start_row, 1)}:{rowcol_to_a1(end_row, width)}"
        ws.update(range_name, values, value_input_option="USER_ENTERED")
        return len(values)
