# Design and engineering references

This is an independently written local tool. No source, screenshots, datasets or design assets from the projects below are redistributed. The references informed interaction patterns and failure checks, not a claim to replace or outperform these tools.

| Reference | What informed this edition |
| --- | --- |
| [Datasette](https://github.com/simonw/datasette) and its [table/API documentation](https://docs.datasette.io/en/stable/json_api.html) | Keep an inspectable table alongside summaries; make limits and the selected view explicit. |
| [OpenRefine](https://github.com/OpenRefine/OpenRefine) | Start with the user's table and its data-quality questions, rather than a promotional dashboard. Keep transformations and claims understandable. |
| [DuckDB UI documentation](https://duckdb.org/docs/stable/core_extensions/ui.html) | A local analysis workflow can be useful without accounts or a cloud upload. This edition does not provide SQL or a database engine. |
| [GOV.UK file-upload guidance](https://design-system.service.gov.uk/components/file-upload/) and [WAI table pattern](https://www.w3.org/WAI/ARIA/apg/patterns/table/) | A labeled native file picker, explicit errors, semantic tables and keyboard access. |
| [openpyxl security guidance](https://openpyxl.readthedocs.io/en/stable/) | Install defusedxml, preflight archive expansion and bound spreadsheet rows/cells. These checks do not create a parser sandbox. |

[Datasette's release notes](https://github.com/simonw/datasette/releases) describe fixes for duplicated primary-key columns, misleading response success and inconsistent row/page limits. This edition rejects duplicate headings, returns explicit input errors and tests that displayed chart sampling does not change full-table statistics. Those are historical lessons, not claims that current Datasette has the same problems.

Reference documentation was read, not every reference application installed or visually tested. Claude Code led the original interface implementation; Codex implemented and independently checked the parser, calculations and local request boundary. Synthetic tests cover unsafe integer precision, identifier/date inference, worksheet dimension errors, formula handling, stale UI results and upload deadlines.

## Runtime libraries

Python dependencies are installed normally, not vendored. FastAPI and openpyxl use MIT licenses, uvicorn uses BSD-3-Clause and defusedxml uses the PSF license. Consult the distributions for their complete license texts. The application itself is MIT licensed.
