# UoS cell-mark regression fixtures

These three cell crops are derived from the **UoS Data Rescue** dataset by
Middleton and Singh, University of Southampton, distributed under
[Creative Commons Attribution 4.0](https://creativecommons.org/licenses/by/4.0/).
Source: [Zenodo record 15730546](https://zenodo.org/records/15730546).
They retain that license, rather than the application's Apache-2.0 license.
No endorsement by the dataset creators is implied.

Modification: original page images were cropped to the supplied cell annotation
boxes and encoded as PNG. Pixel-identical copies of the October 2026 validation
crops are included here; no reference text was changed. `provenance.json`
records source document/cell IDs, bounding boxes, published reference values,
and SHA-256 hashes. They cover a handwritten 5 reaching a cell boundary, a
printed 7 in a wide cell, and a printed 1 with a horizontal rule on blue paper.

The tests check that visible numbers reach OCR. They do not establish the
accuracy of the dataset's transcriptions or of end-to-end table recognition.
