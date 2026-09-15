# Page-local handwriting suggestions

The first pass retains the complete OCR cascade. A temporary writer profile uses
stable digit examples from distinct cells on the same page. Weak readings,
excluded rows, model conflicts and the cell currently being checked do not
contribute reference examples.

The profile compares segmented glyphs only with existing OCR candidates that
have the same numeric structure and satisfy the recognition rule. At least
three independent examples and supporting verifier evidence are required for a
suggestion. The profile never rewrites a value or confirms it automatically.

The Review panel presents suggestions below its primary actions. **Alt+A** copies
a suggestion to the editor; **Enter** confirms only after the reviewer checks it.
Evidence and alternative readings remain available in Audit.

The profile exists only while processing a page. It does not build a permanent
user database or retrain the OCR models. Supporting agreement is not a calibrated
accuracy probability and may repeat the same recognition error.
