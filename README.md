# TableScan Local

TableScan Local is a desktop application for converting scanned and photographed
tables into editable Excel workbooks. Recognition, template matching, review,
and export run locally on the computer.

The interface is currently available in Russian. The application supports
Windows, macOS, and Ubuntu packaging from a shared Python and Qt codebase.

## Main features

- Import PDF, PNG, JPEG, TIFF, and multi-page documents.
- Detect a table grid or adjust its boundaries manually.
- Create reusable templates with table geometry and additional marked fields.
- Assign numeric, integer, text, range, decimal-place, and allowed-value rules
  to columns or visually selected cell blocks.
- Normalize decimal commas and points to a single numeric Excel value.
- Run a multi-stage offline OCR cascade with bundled ONNX models.
- Review uncertain cells against the corresponding highlighted source region.
- Detect empty and conservatively identified cancelled rows.
- Export a normalized data sheet, a scan-layout matrix, and a detailed audit
  sheet in one `.xlsx` workbook.

## Privacy and repository data policy

The application does not upload documents, cell images, templates, or recognized
values. It does not require an online account and does not download models at
runtime.

This repository intentionally contains no source documents, validation images,
cell crops, manually transcribed answers, OCR run reports, exported workbooks,
local databases, saved user templates, or application data. The `qa/`,
`outputs/`, `release/`, `build*/`, and `dist*/` directories are excluded from
version control. Automated tests use only synthetic in-memory or temporary data.

Bundled ONNX model weights are runtime components of the application. Their
origins and checksums are documented in
[`src/tablescan_local/models/README.md`](src/tablescan_local/models/README.md).

## Requirements

- Python 3.11 or 3.12
- Approximately 1 GB of free space for dependencies and build artifacts
- CPU inference; a discrete GPU is not required

## Run from source

macOS or Linux:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
tablescan-local
```

Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
tablescan-local
```

The application stores imported copies, templates, crops, and job state in the
operating system's per-user application-data directory, outside the repository.
Original documents are never modified.

## Tests

```bash
python -m pip install -e ".[dev]"
pytest
tablescan-local --self-test
```

On Linux without a display server:

```bash
QT_QPA_PLATFORM=offscreen pytest
```

## Packaging

Build the application bundle first:

```bash
pyinstaller --clean --noconfirm packaging/tablescan_local.spec
```

Platform-specific packaging files are provided in `packaging/macos`,
`packaging/windows`, and `packaging/linux`. The GitHub Actions workflow runs the
test suite before producing platform bundles.

## Recognition limitations

Handwriting recognition is probabilistic. Similar handwritten digits, faint or
cropped strokes, damaged scans, and ambiguous marks may require human review.
Template constraints can reject impossible formats, but they are not evidence
of what was written. Review important numeric data against the source before
using an exported workbook.

## License

Application source code is licensed under Apache-2.0. Bundled dependencies and
model weights retain their upstream licenses; see
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
