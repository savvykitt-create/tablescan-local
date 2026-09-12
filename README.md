# TableScan Local

TableScan Local is a desktop application for converting scanned and photographed
tables into editable Excel workbooks. Recognition, template matching, review,
and export run locally on the computer.

The interface is available in English (default), Czech, and Russian. Change it
in Settings → Language; the change applies immediately and is remembered.
The application supports Windows, macOS, and Ubuntu packaging from a shared
Python and Qt codebase.

## Main features

- Import PDF, PNG, JPEG, TIFF, and multi-page documents.
- Detect a table grid or adjust its boundaries manually.
- Create reusable templates with table geometry and additional marked fields.
- Assign numeric, integer, text, range, decimal-place, and allowed-value rules
  to columns or visually selected cell blocks.
- Normalize decimal commas and points to a single numeric Excel value.
- Run a multi-stage offline OCR cascade with bundled ONNX models.
- Optionally recheck disputed numeric cells with Qwen and GLM in Slow mode
  on Windows and macOS; changed values remain available for review.
- Use stable cells on the current page as temporary writer-style evidence for
  ambiguous OCR candidates, without retaining handwriting samples or learning
  from user corrections.
- Review uncertain cells against the corresponding highlighted source region.
- Detect empty and conservatively identified cancelled rows.
- Switch between table and field tabs, with linked source highlights and original-image previews.
- Review with keyboard navigation, handwriting suggestions, and collapsed explanations.
- Use a pastel light theme or a dark theme; the selection is saved locally.
- Choose compact Excel export (source-layout sheets only), or extended export
  (source-layout sheets, normalized Data, and detailed Audit).

## Privacy and repository data policy

The application does not upload documents, cell images, templates, or recognized
values. Recognition does not require an online account or a network connection.
The standard models are bundled. The optional Slow mode installer downloads its
pinned models once; subsequent recognition uses local files only.

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
- Approximately 3 GB of free space for dependencies and a native application build
- CPU inference; a discrete GPU is not required

## Slow mode (optional)

**Alignment → Grid → Slow mode — additional verification** runs after Maximum
accuracy. It only changes disputed numeric cells when Qwen and GLM agree on a
rule-compatible reading. Excluded rows and manual corrections are preserved.

On **Windows**, install 64-bit Python 3.12 including its launcher, then open
**Start → TableScan Local → Install or repair slow mode**. Choose Auto, CPU only,
or NVIDIA CUDA. The shortcut installs dependencies, downloads models, and tests
both models. No terminal commands or administrator rights are needed.

From a source checkout, the equivalent Windows PowerShell command is:

```powershell
py -3.12 packaging/install_slow_mode.py --device auto
```

On **Apple Silicon macOS**, the existing MLX installation is still supported:

```bash
python3.12 packaging/install_slow_mode.py
```

The Windows CPU backend uses the original BF16 weights. Plan for at least 16 GB
RAM (24 GB or more recommended for large tables), about 15 GB free disk space,
and potentially long processing times. NVIDIA acceleration needs a compatible
updated driver and enough free VRAM. Auto falls back to CPU when unavailable or
out of GPU memory. These are planning estimates, not guarantees for every table.
The Mac MLX backend uses approximately 5 GB of weights plus dependencies.

Internet is needed only to install the optional module. Documents stay local.
Models survive application updates. The normal installer includes the setup
shortcut but does not include these large models.

See [Slow mode setup and behavior](docs/slow-mode.md) and
[0.7.12 changes and validation](docs/release-0.7.12.md).

## Run from source

macOS or Linux:

```bash
git clone https://github.com/savvykitt-create/tablescan-local.git
cd tablescan-local
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
tablescan-local
```

Windows PowerShell (activation is not required):

```powershell
git clone https://github.com/savvykitt-create/tablescan-local.git
cd tablescan-local
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -e .
.venv\Scripts\tablescan-local.exe
```

The application stores imported copies, templates, crops, and job state in the
operating system's per-user application-data directory, outside the repository.
Original documents are never modified.

For update run:

```
git pull --ff-only
.venv\Scripts\python.exe -m pip install -e .
```

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

## Review workspace (0.7.1)

The lower preview now comes directly from the original page using the selected
cell or field coordinates. OCR masks and retry crops remain available to the
recognition pipeline but are never substituted for the source image in review.
Existing saved recognition results benefit immediately; rerunning OCR is not required.

- **Enter** in the value input confirms and advances; **Alt+Right** moves to the
  next disputed value without confirming. Navigation wraps across pages.
- **Alt+A** inserts a handwriting suggestion for inspection. Explicit confirmation
  is still required before it changes the result.
- **Why review is needed** expands grouped reasons, value rules, alternative
  readings, and technical checks. It is collapsed initially.
- Excluding a row blanks its measurements; clearing the checkbox restores them.
- The original document supports panning, zoom buttons, and **Fit**.
- Both export modes require completed review and retain the same value-rule checks.
  Compact export preserves the grid and blank excluded measurements on one sheet
  per page. Extended export retains the existing Data and Audit sheets.

The application icon uses the supplied SavvyKit logo. Source documents, OCR
models, and recognition policy are unaffected by this interface update.

## Interface languages (0.7.2)

English is the default for installations without a saved language choice,
regardless of the operating system language. Settings → Language offers
English, Čeština, and Русский. Switching language updates existing controls,
dialogs, tooltips, and review explanations without rebuilding the workspace.
The choice is stored alongside the theme in the local preferences file.

The review selection, entered value, template edits, and document images are
preserved. Document text, custom names, and existing audit evidence are data:
they are not translated. Generated names become ordinary text when they are
created. Numeric parsing and the review/export checks are identical in all
three languages. Source-layout worksheet titles use the selected language;
Data and Audit retain their established names and column schema.

Contributor notes: [Localization](docs/LOCALIZATION.md).

## Digit segmentation (0.7.3)

The independent digit verifier and page-local handwriting comparison now preserve
complete digit groups when clear whitespace separates them. This avoids cutting
a wide glyph in half when it follows or precedes a narrow glyph, such as a handwritten
1. Small detached marks do not count as complete digit groups. The existing fallback
remains available when whitespace does not establish the expected segmentation.

There are no fixed substitutions between 1/7/2 or 4/9/6. Handwriting suggestions
still require explicit confirmation.

For an isolated first-run check, set `TABLESCAN_DATA_DIR` to an empty temporary
directory before launching the application. Reinstalling the source code alone
does not erase the existing per-user document database or preferences.

## Template editor and automatic numeric recovery (0.7.6)

- Workflow steps enable only when their prerequisites are met; Export opens the export flow.
- Rotate works in the template editor. Fields and table boundaries can be resized directly on the image, and grid spacing follows boundary changes.
- Rules contains column defaults; the separate Columns tab is removed. Fields and Rules scroll without compressing controls.
- Corner notifications confirm completed actions, and repeated explanatory labels have been removed.
- Missing decimal separators are recovered automatically when the numeric format specifies their placement. Inferred or ambiguous readings still require review.
- New template, field, column, rule, and copy names use English in every interface language. Existing saved names are preserved.
