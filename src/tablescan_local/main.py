from __future__ import annotations

import sys

import cv2
import numpy as np

from .ui import create_application


def self_test() -> int:
    """Load the packaged OCR runtime and run one in-memory recognition pass."""
    from .ocr import LocalOcrEngine, OcrValue, constrain_reading
    from .constraints import ValueConstraints

    image = np.full((96, 320, 3), 255, dtype=np.uint8)
    cv2.putText(image, "123.4", (18, 68), cv2.FONT_HERSHEY_SIMPLEX, 1.8, (0, 0, 0), 3, cv2.LINE_AA)
    result = LocalOcrEngine().recognize_cell(image, numeric=True)
    if result.text != "123.4" or "numeric_verification_required" not in result.flags:
        raise RuntimeError(f"Packaged OCR self-test failed: {result.text!r}")
    precise = LocalOcrEngine(high_accuracy=True).recognize_cell(
        image, numeric=True,
        constraints=ValueConstraints("numeric", minimum=0, maximum=200, decimal_places=1),
    )
    if precise.text != "123.4" or "high_accuracy_consensus" not in precise.flags:
        raise RuntimeError(f"Packaged high-accuracy OCR self-test failed: {precise.text!r}")
    recovered = constrain_reading(
        OcrValue("3417", .99),
        ValueConstraints("numeric", minimum=0, maximum=60, decimal_places=1, suggest_missing_decimal=True, require_decimal=True),
    )
    if recovered.text != "34.7" or "separator_reclassified_from_one" not in recovered.flags:
        raise RuntimeError(f"Template decimal self-test failed: {recovered.text!r}")
    return 0


def slow_mode_self_test() -> int:
    """Exercise both external models and packaged IPC without a GUI or user data."""
    import json
    import tempfile
    import shutil
    from datetime import datetime, timezone
    from pathlib import Path
    from .domain import CellResult, PageResult, TableTemplate, NormalizedRect, CellRuleRegion
    from .constraints import ValueConstraints
    from .slow_mode import runtime_config, refine_page
    from .slow_runtime import runtime_root
    image = np.full((100, 600, 3), 255, np.uint8)
    for x, value in [(20, '12.3'), (320, '45.6')]:
        cv2.putText(image, value, (x, 70), cv2.FONT_HERSHEY_SIMPLEX, 1.8, (0, 0, 0), 3, cv2.LINE_AA)
    template = TableTemplate('self-test', 'Self-test', NormalizedRect(0, 0, 1, 1), [0, 1], [0, .5, 1])
    template.row_label_columns = 0
    template.ensure_column_rules()
    template.cell_rules = [CellRuleRegion('values', 'Values', 0, 0, 0, 1,
        ValueConstraints('numeric', minimum=0, maximum=70, decimal_places=1))]
    page = PageResult(0, 'synthetic', [CellResult(0, c, '99.9', '99.9', .5, flags=['model_disagreement']) for c in range(2)], [])
    report = runtime_root() / 'self-test'
    report.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='tablescan-slow-selftest-') as directory:
        try:
            refine_page(image, page, template, Path(directory), runtime_config())
            if page.slow_mode.get('status') != 'complete' or [c.final_text for c in page.cells] != ['12.3', '45.6']:
                raise RuntimeError(f'Slow-mode self-test failed: {page.slow_mode}')
            summary = {'status': 'passed', 'slow_mode': page.slow_mode}
        except Exception as exc:
            summary = {'status': 'failed', 'error': str(exc), 'slow_mode': page.slow_mode}
        finally:
            shutil.copytree(directory, report, dirs_exist_ok=True)
        summary['timestamp'] = datetime.now(timezone.utc).isoformat()
        (report / 'result.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
        print(json.dumps(summary))
        if summary['status'] != 'passed':
            return 1
    return 0


def main() -> int:
    if "--slow-mode-self-test" in sys.argv:
        return slow_mode_self_test()
    if "--self-test" in sys.argv:
        return self_test()
    app, window = create_application()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
