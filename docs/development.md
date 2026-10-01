# Development

For ready-to-use installers and the application workflow, see the
[README](../README.md). This guide covers running and building from source.

## Requirements

- Git and Python 3.11 or 3.12; use **Python 3.12** for the optional Slow mode runtime.
- Approximately 3 GB of free space for dependencies and a native build,
  excluding optional Slow mode models.
- Build on the target operating system. Standard recognition runs on CPU.

## Run from source

### macOS or Linux

```bash
git clone https://github.com/savvykitt-create/tablescan-local.git
cd tablescan-local
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
tablescan-local
```

### Windows PowerShell

Activation is not required:

```powershell
git clone https://github.com/savvykitt-create/tablescan-local.git
cd tablescan-local
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -e .
.venv\Scripts\tablescan-local.exe
```

For optional models, see [Slow mode setup](slow-mode.md).

## Update

Close the application and run these commands from the repository directory.

macOS or Linux:

```bash
git pull --ff-only
.venv/bin/python -m pip install -e .
```

Windows PowerShell:

```powershell
git pull --ff-only
.venv\Scripts\python.exe -m pip install -e .
```

Application data lives outside the repository. Updating the checkout or virtual
environment does not reset saved documents, templates or preferences.

## Tests

Using the project's virtual environment:

```bash
python -m pip install -e ".[dev]"
pytest
tablescan-local --self-test
```

On Linux without a display server:

```bash
QT_QPA_PLATFORM=offscreen pytest
```

Set `TABLESCAN_DATA_DIR` to an empty temporary directory for an isolated first-run
check. The [CI workflow](../.github/workflows/ci.yml) lists the native build
steps and Linux GUI libraries. Slow mode has [separate verification](slow-mode.md#verification).

## Packaging

Install the development dependencies above, then build the application bundle:

```bash
python -m pip install -c packaging/constraints.txt -e ".[dev]"
pyinstaller --clean --noconfirm packaging/tablescan_local.spec
python packaging/verify_bundle.py dist
```

Public macOS packages use ad hoc signing by default. Developer ID signing and
notarization are [optional](macos-release-signing.md); enable `sign_macos` in the
release workflow only when Apple credentials are configured.

Platform packaging lives in [macOS](../packaging/macos),
[Windows](../packaging/windows) and [Linux](../packaging/linux).
CI tests the source and packaged application before uploading installers to a
release. Run model and hardware checks on each target platform.

## Repository data policy

Keep source documents, manually transcribed answers, validation images, cell
crops, exported workbooks, OCR reports, local databases and saved user templates
out of version control. Automated tests use synthetic or temporary data.
`qa/`, `outputs/`, `release/`, `build*/` and `dist*/` are excluded from version control.
Bundled ONNX weights are runtime components; their
[origins and checksums](../src/tablescan_local/models/README.md) are documented.

## Adaptive performance

Installing from source compiles an optional Cython CTC decoder when a C compiler
is available (Xcode Command Line Tools on macOS, MSVC on Windows). Builds without
a compiler retain the Python decoder. Both use the same beam width, probabilities,
grammar and stable tie-breaking; no recognition passes are removed.

High accuracy and Slow analysis can use up to six isolated CPU cell workers.
The budget depends on available RAM, physical cores and document size; at least
3 GiB or 20% of total memory is reserved, with a further 768 MiB allowance per
worker. Small jobs, GPU OCR and unknown/low-memory systems stay sequential.
Workers preserve ONNX's numerical/thread settings but disable idle thread-pool
busy-waiting. They are stopped under memory pressure or on
cancellation; unfinished cells fall back to the original sequential engine after
a worker failure. Document queue ordering remains unchanged.

Slow analysis owns one external runtime per document. Qwen/GLM weights may remain
loaded between requests only while memory permits. Loading another model checks
both host RAM and CUDA VRAM. Low-memory systems evict weights between stages;
all models are released at document completion or cancellation. Prompts and
generation caches are not shared between requests. Existing Slow installations
work without downloading models or reinstalling their environment.

Saved results include `performance` metadata (decoder, initial resource snapshot,
worker budget, fallback count, primary page durations and total time). The same
summary is written to `crops/performance.json`. Slow execution evidence records
model reuse, load time, inference time in the response files, and request time.
Local performance tests and source-document benchmarks belong in ignored `qa/`.

## Further reading

- [Localization](LOCALIZATION.md)
- [Slow mode setup and CPU compatibility](slow-mode.md)
