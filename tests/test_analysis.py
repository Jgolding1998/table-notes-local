import io
import json
import unittest
import zipfile
from datetime import date

from openpyxl import Workbook

from table_notes.analysis import TableError, analyze, example_csv, parse_table


def workbook_bytes(rows, *, second=False):
    workbook = Workbook()
    for row in rows:
        workbook.active.append(row)
    if second:
        workbook.create_sheet("Another sheet")
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


class AnalysisTests(unittest.TestCase):
    def test_example_statistics_reconcile(self):
        result = analyze(example_csv(), "example.csv", is_example=True)
        self.assertEqual(result["shape"], {"rows": 24, "columns": 4})
        self.assertEqual(result["quality"]["missing_cells"], 2)
        amount = next(column for column in result["columns"] if column["name"] == "amount")
        self.assertEqual(
            amount["stats"]["sum"], sum(40 + i * 3 for i in range(24) if i not in (5, 17))
        )
        self.assertEqual(len(result["series"][0]["forecast"]), 3)
        json.dumps(result, allow_nan=False)

    def test_ids_booleans_and_dates_are_distinct(self):
        result = analyze(
            b"customer_id,code,active,date,amount\n123456,0012,true,2026-01-01,2\n234567,0013,false,2026-01-02,4\n",
            "data.csv",
        )
        self.assertEqual(
            [column["kind"] for column in result["columns"]],
            ["text", "text", "boolean", "date", "number"],
        )
        self.assertEqual(result["preview"]["rows"][0][1], "0012")
        self.assertEqual(result["series"][0]["date_column"], "date")

    def test_quality_counts_and_empty_column(self):
        result = analyze(b"label,amount,empty\nA,2,\nA,2,\nB,,\n", "data.csv")
        self.assertEqual(result["quality"]["duplicate_rows"], 1)
        self.assertEqual(result["quality"]["missing_cells"], 4)
        self.assertEqual(result["quality"]["empty_columns"], ["empty"])

    def test_unsafe_integers_are_text_and_never_rounded(self):
        result = analyze(
            b"amount\n9007199254740993\n10000000000000001\n-10000000000000000\n", "data.csv"
        )
        self.assertEqual(result["columns"][0]["kind"], "text")
        self.assertIsNone(result["columns"][0]["stats"])
        self.assertEqual(result["preview"]["rows"][0][0], "9007199254740993")
        self.assertIn("precision", result["quality"]["warnings"][0])
        self.assertEqual(result["series"], [])

    def test_camel_case_ids_do_not_create_trend_lines(self):
        result = analyze(
            b"customerId,accountNumber,SKU,amount\n123,456,789,2\n124,457,790,3\n", "data.csv"
        )
        self.assertEqual(
            [column["kind"] for column in result["columns"]], ["text", "text", "text", "number"]
        )
        self.assertEqual([series["column"] for series in result["series"]], ["amount"])

    def test_large_integer_aggregate_stays_exact_in_json(self):
        result = analyze(b"amount\n9007199254740990\n5\n", "data.csv")
        self.assertEqual(result["columns"][0]["stats"]["sum"], "9007199254740995")
        self.assertIn("large total", result["quality"]["warnings"][0])

    def test_xlsx_array_formula_displayed_as_text(self):
        from openpyxl.worksheet.formula import ArrayFormula

        data = workbook_bytes([["formula"], [ArrayFormula(ref="A2", text="=SUM(1,2)")]])
        result = analyze(data, "sample.xlsx")
        self.assertEqual(result["preview"]["rows"][0][0], "=SUM(1,2)")
        self.assertIn("never evaluated", result["quality"]["warnings"][0])

    def test_quotes_unicode_and_multiline_csv(self):
        result = analyze('label;amount\n"Caf\u00e9; one\nline";4\n'.encode(), "data.csv")
        self.assertEqual(result["shape"], {"rows": 1, "columns": 2})
        self.assertIn("\n", result["preview"]["rows"][0][0])

    def test_formula_and_html_remain_text(self):
        result = analyze(b"label,amount\n<img src=x onerror=alert(1)>,=1+1\n", "data.csv")
        self.assertEqual(result["columns"][1]["kind"], "text")
        self.assertIn("never evaluated", result["quality"]["warnings"][0])
        self.assertEqual(result["preview"]["rows"][0][1], "=1+1")

    def test_malformed_and_bounded_input(self):
        cases = [
            (b"", "x.csv"),
            (b"a,a\n1,2", "x.csv"),
            (b"a,A\n1,2", "x.csv"),
            (b"a\n1,2", "x.csv"),
            (b"a\n", "x.csv"),
            (b"a\n\x00", "x.csv"),
            (b"a\n\xff", "x.csv"),
            (b"a\n1", "x.xls"),
            (b"a\n1e999", "x.csv"),
            (b"a\n" + b"x" * 4097, "x.csv"),
            (b"a\n" + b"1\n" * 20001, "x.csv"),
            (b"x" * (5 * 1024 * 1024 + 1), "x.csv"),
        ]
        for data, name in cases:
            with self.subTest(length=len(data), name=name), self.assertRaises(TableError):
                analyze(data, name)

    def test_invalid_dates_not_silently_used_as_dates(self):
        result = analyze(b"date,value\n2026-02-30,2\n2026-03-01,3\n", "data.csv")
        self.assertEqual(result["columns"][0]["kind"], "text")
        self.assertIsNone(result["series"][0]["date_column"])

    def test_downsample_preserves_endpoints_and_full_stats(self):
        data = ("amount\n" + "\n".join(str(i) for i in range(500))).encode()
        result = analyze(data, "data.csv")
        series = result["series"][0]
        self.assertEqual(len(series["points"]), 120)
        self.assertEqual([series["points"][0]["value"], series["points"][-1]["value"]], [0, 499])
        self.assertEqual(result["columns"][0]["stats"]["sum"], sum(range(500)))
        self.assertEqual(series["direction"], "up")

    def test_xlsx_first_sheet_formulas_and_dates(self):
        data = workbook_bytes(
            [
                ["date", "amount", "formula"],
                [date(2026, 1, 1), 2, "=1+1"],
                [date(2026, 1, 2), 4, "=2+2"],
            ],
            second=True,
        )
        result = analyze(data, "sample.xlsx")
        self.assertEqual(
            [column["kind"] for column in result["columns"]], ["date", "number", "text"]
        )
        self.assertIn("first worksheet", result["quality"]["warnings"][0])
        self.assertEqual(result["preview"]["rows"][0][2], "=1+1")
        self.assertEqual(result["columns"][0]["stats"], {"min": "2026-01-01", "max": "2026-01-02"})

    def test_xlsx_bad_dimensions_cannot_hide_extra_columns(self):
        source = workbook_bytes([["a", "b"], [1, 2]])
        output = io.BytesIO()
        with (
            zipfile.ZipFile(io.BytesIO(source)) as original,
            zipfile.ZipFile(output, "w") as target,
        ):
            for item in original.infolist():
                value = original.read(item)
                if item.filename == "xl/worksheets/sheet1.xml":
                    value = value.replace(b'ref="A1:B2"', b'ref="A1:A1"')
                target.writestr(item, value)
        self.assertEqual(parse_table(output.getvalue(), "data.xlsx")[:2], (["a", "b"], [[1, 2]]))

    def test_archive_expansion_and_bad_workbooks_refused(self):
        source = io.BytesIO()
        with zipfile.ZipFile(source, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("large.xml", b" " * (3 * 1024 * 1024))
        for data in (b"not a zip", source.getvalue()):
            with self.assertRaises(TableError):
                analyze(data, "data.xlsx")


if __name__ == "__main__":
    unittest.main()
