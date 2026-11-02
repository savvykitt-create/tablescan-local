"""Recognize complete protocols when a scan contains a reduced, shifted form."""
import json
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
import pytest

from tablescan_local.domain import TableTemplate
from tablescan_local.documents import open_document
from tablescan_local.imaging import detect_grid
from tablescan_local.template_fit import fit_template
from tablescan_local.template_matcher import rank_templates


TEMPLATES = Path(__file__).parents[1] / 'src/tablescan_local/default_templates'


def template(name):
    return TableTemplate.from_dict(json.loads((TEMPLATES / f'{name}.json').read_text()))


@lru_cache(maxsize=2)
def reference_image(name):
    return open_document(TEMPLATES / f'{name}.pdf')[0]


@pytest.mark.parametrize('name', ['plantar', 'von_frey'])
@pytest.mark.parametrize('scale', [.25, .35, .55, .8])
@pytest.mark.parametrize('angle', [-.3, 0, .3])
def test_reduced_shifted_scan_keeps_outer_columns_and_template_rules(name, scale, angle):
    master = template(name)
    original = master.to_dict()
    source = reference_image(name)
    height, width = source.shape[:2]
    # Area resampling retains faint printed rules at reduced scan resolution.
    small = cv2.resize(source, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    image = np.full_like(source, 255)
    x, y = round(width * (.95-scale)), round(height * (.95-scale))
    image[y:y+small.shape[0], x:x+small.shape[1]] = small
    transform = cv2.getRotationMatrix2D((width/2, height/2), angle, 1)
    image = cv2.warpAffine(image, transform, (width, height), borderValue=(255, 255, 255))

    detected = detect_grid(image)
    assert not detected.warnings
    assert len(detected.row_guides) == master.rows + 1
    assert len(detected.column_guides) == master.columns + 1
    fitted = fit_template(master, detected, width/height)
    assert fitted.column_guides == detected.column_guides
    assert fitted.row_guides == detected.row_guides
    assert master.to_dict() == original
    for row in (1, 20):
        for column in range(1, 11):
            rule, _ = fitted.cell_constraints(row, column)
            assert rule.decimal_places == 1 and rule.require_decimal
            assert rule.minimum == 0
            assert rule.maximum == (35 if name == 'plantar' else 200)
    assert fitted.cell_constraints(1, 0)[0].value_format == 'integer'
    assert fitted.cell_constraints(1, 11)[0].value_format == 'text'
    # Title recognition is exercised separately by PDF import integration.
    # Here only the matching decision is tested with a known printed title.
    detected.title_text = master.name
    matches = rank_templates(image.shape, detected, [template('staircase'), template('von_frey'), template('plantar')])
    assert matches[0].template.id == master.id
    assert matches[0].score >= .8


@pytest.mark.parametrize('columns, rows', [(1, 1), (3, 1), (1, 3)])
def test_small_metadata_box_or_underline_is_not_a_table(columns, rows):
    image = np.full((1800, 2600, 3), 255, np.uint8)
    for x in np.linspace(1700, 2200, columns+1):
        cv2.line(image, (round(x), 1200), (round(x), 1450), (30, 30, 30), 2)
    for y in np.linspace(1200, 1450, rows+1):
        cv2.line(image, (1700, round(y)), (2200, round(y)), (30, 30, 30), 2)
    with pytest.raises(ValueError, match='No table grid'):
        detect_grid(image)


@pytest.mark.parametrize('merged', [False, True])
def test_reduced_table_keeps_a_genuine_short_header(merged):
    image = np.full((1800, 2600, 3), 255, np.uint8)
    xs = np.linspace(1650, 2250, 13).astype(int)
    ys = [1100, 1107, *range(1127, 1288, 20)]
    for index, x in enumerate(xs):
        start = ys[1] if merged and index not in (0, len(xs)-1) else ys[0]
        cv2.line(image, (x, start), (x, ys[-1]), (25, 25, 25), 1)
    for y in ys:
        cv2.line(image, (xs[0], y), (xs[-1], y), (25, 25, 25), 1)
    detected = detect_grid(image)
    assert [round(y * image.shape[0]) for y in detected.row_guides] == ys
    assert len(detected.column_guides) == len(xs)
