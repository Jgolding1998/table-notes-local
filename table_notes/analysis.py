"""Bounded table parsing and descriptive statistics. No network or data storage."""

from __future__ import annotations

import csv
import io
import json
import math
import re
import statistics
import zipfile
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_ROWS = 20_000
MAX_COLUMNS = 60
MAX_CELL = 4096
MAX_SAFE_INTEGER = 2**53 - 1
NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\Z")
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ][0-9:.+Z-]+)?\Z")
IDENTIFIER = re.compile(
    r"(?:^(?:id|sku)$|(?:^|[_ ])(?:id|identifier|code|zip|phone|account|number|no)$)", re.I
)


class TableError(ValueError):
    """An expected input error safe to display without parser internals."""


def _cell(value):
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if not math.isfinite(value) or abs(value) > 1e100:
            raise TableError("A number is outside the supported finite range (-1e100 to 1e100).")
        return str(value) if abs(value) > MAX_SAFE_INTEGER else value
    if not isinstance(value, str):
        from openpyxl.worksheet.formula import ArrayFormula

        if isinstance(value, ArrayFormula) and isinstance(value.text, str):
            value = value.text
        else:
            raise TableError(
                "A workbook cell uses an unsupported type. Save a CSV of values instead."
            )
    value = str(value)
    if len(value) > MAX_CELL or "\0" in value:
        raise TableError(
            "A cell is too long or contains a null character. Limit cells to 4,096 characters."
        )
    return value if value.strip() else None


def _rows_to_table(rows):
    try:
        header = next(rows)
    except StopIteration:
        raise TableError(
            "The file is empty. Include a header row and at least one data row."
        ) from None
    if len(header) > MAX_COLUMNS:
        raise TableError("Use at most 60 columns.")
    while header and header[-1] is None:
        header = header[:-1]
    if not header or len(header) > MAX_COLUMNS:
        raise TableError("Use between 1 and 60 columns, with a name for each column.")
    names = [str(value).strip() if value is not None else "" for value in header]
    if any(not name or len(name) > 80 or not name.isprintable() for name in names):
        raise TableError("Every column needs a readable name of 1 to 80 characters.")
    if len({name.casefold() for name in names}) != len(names):
        raise TableError(
            "Column names must be unique, ignoring capitalization and surrounding spaces."
        )
    result = []
    for row_number, row in enumerate(rows, 1):
        if row_number > MAX_ROWS:
            raise TableError("The table exceeds 20,000 rows. Split it into smaller files.")
        if len(row) > len(names) and any(value not in (None, "") for value in row[len(names) :]):
            raise TableError(
                "A row has more values than the header. Check the delimiter and column names."
            )
        values = [_cell(value) for value in row[: len(names)]]
        if not any(value is not None for value in values):
            continue
        result.append(values + [None] * (len(names) - len(values)))
    if not result:
        raise TableError("There are no data rows beneath the header.")
    return names, result


def parse_table(data: bytes, filename: str):
    if not data or len(data) > MAX_FILE_BYTES:
        raise TableError("Choose a nonempty CSV or XLSX file up to 5 MiB.")
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if extension == "csv":
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise TableError("Save the CSV as UTF-8, or choose an XLSX workbook.") from None
        if "\0" in text:
            raise TableError("The CSV contains null characters. Save it as UTF-8 text.")
        try:
            dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        try:
            names, rows = _rows_to_table(
                iter(csv.reader(io.StringIO(text, newline=""), dialect, strict=True))
            )
        except csv.Error:
            raise TableError(
                "The CSV could not be read. Check its quotes, delimiter and cell sizes."
            ) from None
        return names, rows, []
    if extension != "xlsx":
        raise TableError("Choose a .csv or .xlsx file. Other workbook formats are not supported.")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > 256 or len({entry.filename for entry in entries}) != len(entries):
                raise TableError("The workbook contains too many or duplicate archive members.")
            if sum(entry.file_size for entry in entries) > 25 * 1024 * 1024:
                raise TableError("The workbook expands beyond 25 MiB. Save a smaller worksheet.")
            for entry in entries:
                if entry.flag_bits & 1 or entry.file_size > 12 * 1024 * 1024:
                    raise TableError("Encrypted or oversized workbook members are not supported.")
                if entry.file_size > max(2 * 1024 * 1024, entry.compress_size * 150):
                    raise TableError("The workbook has an unusually high compression ratio.")
                if ".." in entry.filename.split("/") or "\\" in entry.filename:
                    raise TableError("The workbook contains an invalid archive path.")
        from openpyxl import load_workbook

        workbook = load_workbook(
            io.BytesIO(data), read_only=True, data_only=False, keep_links=False
        )
        try:
            if not workbook.worksheets:
                raise TableError("The workbook has no worksheets.")
            sheet = workbook.worksheets[0]
            if (sheet.max_row or 0) > MAX_ROWS + 1 or (sheet.max_column or 0) > MAX_COLUMNS:
                raise TableError("The first worksheet exceeds 20,000 data rows or 60 columns.")
            # Workbook dimensions can be wrong; don't silently discard cells
            # outside a producer's declared rectangle.
            sheet.reset_dimensions()
            names, rows = _rows_to_table(iter(sheet.iter_rows(values_only=True)))
            warnings = (
                ["Only the first worksheet was analyzed."] if len(workbook.worksheets) > 1 else []
            )
            return names, rows, warnings
        finally:
            workbook.close()
    except TableError:
        raise
    except Exception:
        raise TableError(
            "The workbook could not be read. Use an unencrypted .xlsx file with a header row."
        ) from None


def _parse_number(value):
    if isinstance(value, bool) or value is None:
        return None, None
    if isinstance(value, (int, float)):
        return (value, None) if abs(value) <= MAX_SAFE_INTEGER else (None, "precision")
    value = str(value).strip()
    if not NUMBER.fullmatch(value) or re.match(r"[+-]?0\d", value):
        return None, None
    try:
        decimal = Decimal(value)
    except InvalidOperation:
        raise TableError("A number has an unsupported exponent. Use a smaller exponent.") from None
    number = float(value)
    if not math.isfinite(number) or abs(number) > 1e100:
        raise TableError("A number is outside the supported finite range (-1e100 to 1e100).")
    if decimal.copy_abs() > MAX_SAFE_INTEGER:
        return None, "precision"
    if decimal and number == 0:
        return None, "underflow"
    return (int(value) if re.fullmatch(r"[+-]?\d+", value) else number), None


def _number(value):
    return _parse_number(value)[0]


def _date(value):
    if not isinstance(value, str) or not ISO_DATE.fullmatch(value.strip()):
        return None
    try:
        return (
            datetime.fromisoformat(value.strip().replace("Z", "+00:00")).date()
            if len(value.strip()) > 10
            else date.fromisoformat(value.strip())
        )
    except ValueError:
        return None


def analyze(data: bytes, filename: str, *, is_example=False):
    names, rows, warnings = parse_table(data, filename)
    columns = []
    numeric = {}
    date_index = None
    formula_cells = 0
    for index, name in enumerate(names):
        values = [row[index] for row in rows if row[index] is not None]
        formula_cells += sum(
            isinstance(value, str) and value.lstrip().startswith("=") for value in values
        )
        identifier = IDENTIFIER.search(re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name))
        parsed = [_parse_number(value) for value in values] if values and not identifier else []
        numbers = [number for number, _ in parsed]
        if not values:
            kind = "empty"
        elif all(
            isinstance(value, bool) or isinstance(value, str) and value.lower() in ("true", "false")
            for value in values
        ):
            kind = "boolean"
        elif all(_date(value) is not None for value in values):
            kind = "date"
            if date_index is None:
                date_index = index
        elif numbers and all(value is not None for value in numbers):
            kind = "number"
            numeric[index] = numbers
        else:
            kind = "text"
        stats = None
        if kind == "date":
            days = [_date(value) for value in values]
            stats = {"min": min(days).isoformat(), "max": max(days).isoformat()}
        if kind == "number":
            stats = {
                "min": min(numbers),
                "median": statistics.median(numbers),
                "max": max(numbers),
                "mean": statistics.fmean(numbers),
                "sum": math.fsum(numbers),
            }
            total = (
                sum(numbers)
                if all(isinstance(value, int) for value in numbers)
                else math.fsum(numbers)
            )
            # Keep large totals as decimal text so JavaScript cannot silently
            # round an otherwise exact integer aggregate.
            if abs(total) > MAX_SAFE_INTEGER:
                stats["sum"] = str(total)
                warnings.append(
                    f"Column '{name}' has a large total, shown as text to preserve its precision."
                )
            else:
                stats["sum"] = total
        columns.append(
            {
                "name": name,
                "kind": kind,
                "missing": len(rows) - len(values),
                "unique": len({json.dumps(value, ensure_ascii=False) for value in values}),
                "stats": stats,
            }
        )
        if any(issue == "precision" for _, issue in parsed):
            warnings.append(
                f"Column '{name}' contains numbers beyond safe browser precision and is kept as text."
            )
        if any(issue == "underflow" for _, issue in parsed):
            warnings.append(
                f"Column '{name}' contains numbers too small to analyze without rounding to zero "
                "and is kept as text."
            )
    if formula_cells:
        warnings.append(
            f"{formula_cells} formula-like cells are treated as text; formulas are never evaluated."
        )
    missing = sum(column["missing"] for column in columns)
    duplicate_rows = len(rows) - len({json.dumps(row, ensure_ascii=False) for row in rows})
    series = []
    for index in list(numeric)[:12]:
        points = []
        for position, row in enumerate(rows):
            value = _number(row[index])
            if value is None:
                continue
            label = str(position + 1)
            if date_index is not None:
                day = _date(row[date_index])
                if day is None:
                    continue
                label = day.isoformat()
            points.append({"label": label, "value": value})
        if date_index is not None:
            points.sort(key=lambda point: point["label"])
        slope = None
        forecast = []
        direction = "insufficient"
        if len(points) >= 2:
            values = [point["value"] for point in points]
            slope, intercept = statistics.linear_regression(range(len(values)), values)
            epsilon = max(
                (max(values) - min(values)) * 0.01, abs(statistics.fmean(values)) * 1e-12, 1e-12
            )
            fitted_change = slope * (len(values) - 1)
            direction = (
                "up" if fitted_change > epsilon else "down" if fitted_change < -epsilon else "flat"
            )
            if len(points) >= 6:
                forecast = [
                    {
                        "label": f"Next observation {step + 1}",
                        "value": intercept + slope * (len(values) + step),
                    }
                    for step in range(3)
                ]
        displayed = (
            points
            if len(points) <= 120
            else [points[round(i * (len(points) - 1) / 119)] for i in range(120)]
        )
        series.append(
            {
                "column": names[index],
                "date_column": names[date_index] if date_index is not None else None,
                "points": displayed,
                "observations": len(points),
                "direction": direction,
                "slope": slope,
                "forecast": forecast,
                "note": "Straight-line extension by observation, not a reliable prediction. Dates are not resampled; uneven intervals and repeated dates can mislead.",
            }
        )
    narrative = [
        {
            "title": "The table at a glance",
            "body": f"{len(rows):,} rows and {len(names)} columns. Blank rows are ignored.",
        }
    ]
    blank_text = "1 cell is blank" if missing == 1 else f"{missing:,} cells are blank"
    duplicate_text = (
        "1 row exactly repeats"
        if duplicate_rows == 1
        else f"{duplicate_rows:,} rows exactly repeat"
    )
    narrative.append(
        {
            "title": "Gaps to check" if missing else "No blank cells found",
            "body": f"{blank_text}. {duplicate_text} an earlier parsed row.",
        }
    )
    narrative.append(
        {
            "title": "A starting point",
            "body": "Review column types and source data before using statistics or extending a trend.",
        }
    )
    result = {
        "filename": re.split(r"[/\\]", filename)[-1][:160],
        "is_example": is_example,
        "shape": {"rows": len(rows), "columns": len(names)},
        "quality": {
            "missing_cells": missing,
            "duplicate_rows": duplicate_rows,
            "empty_columns": [column["name"] for column in columns if column["kind"] == "empty"],
            "warnings": warnings,
        },
        "columns": columns,
        "preview": {
            "columns": names,
            "rows": [
                [value[:400] if isinstance(value, str) else value for value in row]
                for row in rows[:12]
            ],
        },
        "series": series,
        "narrative": narrative,
    }
    json.dumps(result, allow_nan=False)
    return result


def example_csv():
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["date", "category", "units", "amount"])
    for index in range(24):
        writer.writerow(
            [
                (date(2026, 1, 1) + timedelta(days=index)).isoformat(),
                "Sample A" if index % 2 else "Sample B",
                index + 2,
                "" if index in (5, 17) else 40 + index * 3,
            ]
        )
    return output.getvalue().encode("utf-8")
