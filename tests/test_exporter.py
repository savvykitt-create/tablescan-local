from zipfile import ZipFile

from openpyxl import load_workbook

from tablescan_local.domain import (
    CellResult,
    ColumnRule,
    FieldRegion,
    FieldResult,
    JobResult,
    NormalizedRect,
    PageResult,
    TableTemplate,
)
from tablescan_local.exporter import export_job


def make_job() -> JobResult:
    template = TableTemplate(
        id="t1",
        name="Plantar",
        table_rect=NormalizedRect(0.1, 0.2, 0.8, 0.6),
        row_guides=[0.2, 0.3, 0.4, 0.5],
        column_guides=[0.1, 0.2, 0.4, 0.6],
        header_rows=1,
        row_label_columns=1,
        fields=[
            FieldRegion("test", "Test", NormalizedRect(0.1, 0.1, 0.2, 0.05)),
            FieldRegion(
                "side", "Side", NormalizedRect(0.3, 0.1, 0.2, 0.05), source="fixed",
                fixed_value="Left Hind", export_mode="column_group", column_start=1, column_end=2,
            ),
        ],
        column_rules=[
            ColumnRule("Animal", role="row_label"),
            ColumnRule("Measure 1"),
            ColumnRule("Measure 2"),
        ],
    )
    texts = [
        ["", "1", "2"],
        ["A1", "12.5", "8.2 ± 0.4"],
        ["A2", "", "15%"],
    ]
    cells = [
        CellResult(row, column, value, value, 0.99, status="confirmed")
        for row, values in enumerate(texts)
        for column, value in enumerate(values)
    ]
    fields = [
        FieldResult("test", "Test", "PLANTAR", "PLANTAR", 0.99, status="confirmed"),
        FieldResult("side", "Side", "Left Hind", "Left Hind", 1.0, status="confirmed"),
    ]
    return JobResult("/tmp/source.pdf", template, [PageResult(0, "/tmp/source.pdf", cells, fields)])


def test_export_creates_normalized_data_and_audit(tmp_path) -> None:
    output = export_job(make_job(), tmp_path / "result.xlsx")
    workbook = load_workbook(output, data_only=True)

    assert workbook.sheetnames == ["Data", "Исходная таблица", "Audit"]
    data = workbook["Data"]
    headers = [cell.value for cell in data[1]]
    assert headers == [
        "Source file", "Page", "Test", "Side", "Source row", "Measurement",
        "Value", "Review status", "Confidence",
    ]
    assert data["G2"].value == 12.5
    assert data["G3"].value == "8.2 ± 0.4"
    assert data["D2"].value == "Left Hind"
    layout = workbook["Исходная таблица"]
    assert layout.max_row == 3 and layout.max_column == 3
    assert layout["A2"].value == "A1"
    assert layout["B2"].value == 12.5
    assert layout["C2"].value == "8.2 ± 0.4"
    assert layout.column_dimensions["B"].width > layout.column_dimensions["A"].width
    assert workbook["Audit"].max_row == 12

    with ZipFile(output) as archive:
        sheet_xml = archive.read("xl/worksheets/sheet1.xml")
        assert b"<autoFilter" not in sheet_xml


def test_layout_sheet_keeps_excluded_measurements_empty(tmp_path):
    job = make_job()
    job.pages[0].excluded_rows = [2]
    job.pages[0].cell(2, 1).status = "excluded"
    job.pages[0].cell(2, 2).status = "excluded"

    workbook = load_workbook(export_job(job, tmp_path / "excluded.xlsx"), data_only=True)
    layout = workbook["Исходная таблица"]

    assert layout["A3"].value == "A2"
    assert layout["B3"].value is None and layout["C3"].value is None


def test_export_keeps_raw_and_alternatives_as_literal_text(tmp_path):
    job = make_job()
    item = job.pages[0].cell(1, 1)
    item.raw_text = "=1+1"
    item.alternatives = "125 | I2.5"
    output = export_job(job, tmp_path / "audit.xlsx")
    wb = load_workbook(output, data_only=False)
    rows = list(wb["Audit"].values)
    record = next(r for r in rows[1:] if r[3] == "R2C2")
    assert record[4] == "=1+1"
    assert record[-1] == "125 | I2.5"
    assert all(c.data_type != "f" for row in wb["Audit"] for c in row)


def test_export_blocks_unconfirmed_numeric_and_retains_first_data_row(tmp_path):
    import pytest
    job = make_job()
    job.template.header_rows = 0
    item = job.pages[0].cell(0, 1)
    item.raw_text = item.final_text = "34.4"
    item.status = "automatic"
    item.flags = ["low_confidence"]
    with pytest.raises(ValueError, match="need review"):
        export_job(job, tmp_path / "blocked.xlsx")
    item.status = "confirmed"
    wb = load_workbook(export_job(job, tmp_path / "confirmed.xlsx"))
    assert wb["Data"]["G2"].value == 34.4


def test_confirmed_value_cannot_bypass_hard_rule_on_export(tmp_path):
    import pytest
    job = make_job()
    job.template.column_rules[1].value_format = "numeric"
    job.template.column_rules[1].decimal_places = 1
    job.template.column_rules[1].maximum = 100
    item = job.pages[0].cell(1, 1)
    item.final_text = "125"
    item.applied_rule = "1 decimal place"
    with pytest.raises(ValueError, match="R2C2"):
        export_job(job, tmp_path / "should-not-exist.xlsx")
    assert not (tmp_path / "should-not-exist.xlsx").exists()
    item.final_text = "12.5"
    output = export_job(job, tmp_path / "valid.xlsx")
    rows = list(load_workbook(output)["Audit"].values)
    record = next(row for row in rows[1:] if row[3] == "R2C2")
    assert record[-2] == "1 decimal place"
