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

## Further reading

- [Localization](LOCALIZATION.md)
- [Slow mode setup and CPU compatibility](slow-mode.md)
