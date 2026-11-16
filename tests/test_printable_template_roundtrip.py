"""Opt-in verification through a real spreadsheet print engine.

Set TABLESCAN_TEST_SOFFICE to a LibreOffice soffice executable to run these.
The ordinary workbook tests do not require an installed office application.
"""
import json
import os
from pathlib import Path
import subprocess

import cv2
import numpy as np
import pypdfium2 as pdfium
import pytest

from tablescan_local.domain import TableTemplate
from tablescan_local.imaging import detect_grid
from tablescan_local.printable_templates import export_template_excel, template_page
from tablescan_local.template_fit import fit_template


SOURCE = Path(__file__).parents[1] / "src/tablescan_local/default_templates"
CASES = ["maxspeed", "plantar", "von_frey", "staircase", "staircase_portrait",
         "custom-1.0", "custom-1.1", "custom-0.9"]


@pytest.fixture(scope="module")
def printed_workbooks(tmp_path_factory):
    soffice = os.environ.get("TABLESCAN_TEST_SOFFICE")
    if not soffice:
        pytest.skip("Set TABLESCAN_TEST_SOFFICE to verify actual spreadsheet printing")
    directory = tmp_path_factory.mktemp("printed-template-roundtrip")
    templates = {}
    workbooks = []
    for key in CASES:
        source_key = "plantar" if key.startswith("custom-") else key
        template = TableTemplate.from_dict(json.loads((SOURCE / f"{source_key}.json").read_text()))
        if key.startswith("custom-"):
            template.reference_page_aspect = float(key.removeprefix("custom-"))
        templates[key] = template
        workbooks.append(export_template_excel(template, directory / f"{key}.xlsx"))
    result = subprocess.run(
        [soffice, f"-env:UserInstallation={(directory / 'profile').as_uri()}",
         "--headless", "--convert-to", "pdf", "--outdir", str(directory),
         *(str(path) for path in workbooks)], capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    for key in CASES:
        assert (directory / f"{key}.pdf").is_file(), result.stdout + result.stderr
    return directory, templates


def _printed_rule(mask, coordinate, *, axis, start, end):
    """Locate an actual printed rule near the expected page coordinate."""
    height, width = mask.shape
    extent = width if axis == 0 else height
    cross_extent = height if axis == 0 else width
    lo, hi = round(start * cross_extent), round(end * cross_extent)
    region = mask[lo:hi, :] if axis == 0 else mask[:, lo:hi]
    counts = region.sum(axis=axis)
    left = max(0, round((coordinate - .006) * extent))
    right = min(extent, round((coordinate + .006) * extent) + 1)
    local_counts = counts[left:right]
    # Tiny fields can put both box edges in the search window. Prefer the
    # nearest continuous rule instead of the darkest of those two borders.
    candidates = left + np.flatnonzero(local_counts >= (hi - lo) * .8)
    assert len(candidates), "No continuous printed rule near the expected boundary"
    position = min(candidates, key=lambda candidate: abs(candidate - coordinate * extent))
    return position / extent


@pytest.mark.parametrize("dpi", [200, 288])
@pytest.mark.parametrize("key", CASES)
def test_printed_form_accepts_its_original_template(printed_workbooks, key, dpi):
    directory, templates = printed_workbooks
    template = templates[key]
    before = template.to_dict()
    document = pdfium.PdfDocument(directory / f"{key}.pdf")
    try:
        assert len(document) == 1
        page = document[0]
        size = page.get_size()
        native_page = template_page(template)
        assert size[0] / size[1] == pytest.approx(native_page.width_mm / native_page.height_mm, abs=.0002)
        image = np.asarray(page.render(scale=dpi / 72).to_pil().convert("RGB"))[:, :, ::-1].copy()
    finally:
        document.close()
    mask = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) < 96
    x0, x1 = template.column_guides[0], template.column_guides[-1]
    y0, y1 = template.row_guides[0], template.row_guides[-1]
    for guide in template.column_guides:
        assert _printed_rule(mask, guide, axis=0, start=y0, end=y1) == pytest.approx(guide, abs=.003)
    for guide in template.row_guides:
        assert _printed_rule(mask, guide, axis=1, start=x0, end=x1) == pytest.approx(guide, abs=.003)
    for field in template.fields:
        if field.source != "ocr":
            continue
        r = field.rect
        for guide in (r.x, r.x + r.width):
            assert _printed_rule(mask, guide, axis=0, start=r.y,
                                 end=r.y + r.height) == pytest.approx(guide, abs=.003)
        for guide in (r.y, r.y + r.height):
            assert _printed_rule(mask, guide, axis=1, start=r.x,
                                 end=r.x + r.width) == pytest.approx(guide, abs=.003)
    detection = detect_grid(image)
    assert len(detection.row_guides) == len(template.row_guides)
    assert len(detection.column_guides) == len(template.column_guides)
    fitted = fit_template(template, detection, page_aspect=image.shape[1] / image.shape[0])
    for original, actual in zip(template.fields, fitted.fields):
        for attribute in ("x", "y", "width", "height"):
            assert getattr(actual.rect, attribute) == pytest.approx(getattr(original.rect, attribute), abs=.003)
    assert template.to_dict() == before
