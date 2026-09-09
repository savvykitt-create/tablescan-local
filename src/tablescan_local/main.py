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


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    app, window = create_application()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
