# Slow mode

Enable **Alignment → Grid → Slow mode — additional verification**. This optional
mode also enables Maximum accuracy. Standard recognition remains the default.

## How it works

1. Run the existing local OCR, cancellation detection, writer adaptation, and outlier checks.
2. Qwen reads the table using synthetic row positions, without identifier columns.
3. GLM reads original row images only where Qwen proposes a different admissible number for a disputed cell.
4. Replace a value only when both models agree after applying the current cell rules.
5. Preserve excluded rows/cells, confirmed values, and manual corrections. Headers, text fields, and identifiers are outside this mode.
6. Keep changed values in review, with the original reading and both model responses available under **Why review is needed**.

The models can agree on an incorrect reading. This is additional evidence, not
an automatic confirmation. Malformed or unsupported responses leave the original
value in place. A failed or incomplete verification is reported explicitly; base
OCR results are retained. Cancellation terminates the active model process.

Qwen and GLM run sequentially in separate processes. The main Qt/ONNX application
does not load Metal, PyTorch or CUDA libraries. Full requests, model responses, and crops remain
inside the local job's `slow-mode` directory.

## Install once

### From application Settings

On Windows or Apple Silicon macOS, install TableScan, then open
**Settings → Slow mode → Install Slow mode**. The application downloads Python
3.12 automatically if needed, installs the dependencies and both models, and
checks both models locally. No terminal or separate Python installation is
required. Python is prepared for the current user without changing PATH.

The application creates its own virtual environment in the Slow data folder
(`slow-mode/runtime`) and installs all optional dependencies there. Model workers
use that environment automatically; no activation is needed. The main application
uses its bundled Python and dependencies. System Python packages are not modified.

Settings shows whether the module is missing, installing, ready, or needs repair.
Installation runs in the background; expand **Show installation details** for
the download and setup log. **Cancel installation** stops the download, dependency
installation, or model verification and its child processes. Wait for the cancelled
status before closing TableScan. After cancellation or failure, use **Retry
installation**; completed model files are reused. Cancellation may wait briefly for an in-flight network operation to return or
time out. The ready status is shown only after setup
and model verification succeed. You can select Slow analysis immediately, or
close and reopen TableScan; the installation persists across launches.

Allow at least 15 GB of free disk space and internet access for installation.
The progress bar shows downloaded bytes for Python and activity during package
and model setup. Full model verification can take a long time on CPU.
Documents are not uploaded. Recognition works offline after setup.

The Windows bootstrap uses the official Python 3.12.10 x64 installer and verifies its pinned SHA-256
from the Python release SBOM before running it. Apple Silicon macOS uses the
[standalone CPython distribution](https://github.com/astral-sh/python-build-standalone)
(Python 3.12.14, build 20260901), verifies its pinned SHA-256, and extracts it
inside the application data folder. Other platforms require an existing Python 3.12.

### Alternative manual installation

Python 3.12 is required. On Windows use the installed Start-menu shortcut
**Install or repair slow mode** or run from a checkout:

```powershell
py -3.12 packaging/install_slow_mode.py --device auto
```

Use `--device cpu` to install the smaller CPU-only PyTorch build, or `--device
cuda` to explicitly require NVIDIA. Auto installs CUDA wheels if `nvidia-smi` is
available, otherwise CPU wheels. During recognition it checks CUDA availability
and free VRAM separately for each model (12 GiB for Qwen, 4 GiB for GLM), and can
retry on CPU after CUDA out-of-memory. Explicit CUDA reports failure instead of
silently changing devices. AMD/Intel graphics currently use the CPU path.

CPU defaults to BF16 to limit RAM use; budget at least 16 GB RAM and preferably
24 GB or more. `--cpu-dtype float32` is a compatibility option requiring roughly
32 GB RAM. CPU time varies greatly with processor instructions and image size;
there is no promised per-table time. Workers allow up to two hours for Qwen and
at least two hours for GLM (ten minutes per requested row for larger batches).
Cancellation remains available throughout inference and stops the worker.

Apple Silicon Macs retain the validated MLX backend:

```bash
python3.12 packaging/install_slow_mode.py
```

Intel Macs and Linux default to the same Transformers backend as Windows.
These additional platforms require their own performance validation. You can
explicitly choose `--backend transformers` for development on Apple Silicon.

The installer pins dependencies and model revisions in
`src/tablescan_local/slow_runtime.py` and `packaging/install_slow_mode.py`.
PyTorch 2.10.0 / torchvision 0.25.0 and Transformers 5.17.0 implement the Windows
path without requiring Flash Attention, custom CUDA extensions or model code
from the network. GPU wheels use CUDA 12.8; no separate CUDA toolkit is required.
CPU uses published BF16 weights; CUDA uses BF16 when supported, otherwise FP16.

Runtime locations:

- Windows: `%LOCALAPPDATA%\TableScan Local\slow-mode`
- macOS: `~/Library/Application Support/TableScan Local/slow-mode`
- Linux: `$XDG_DATA_HOME/tablescan-local/slow-mode` (default `~/.local/share`)

`TABLESCAN_SLOW_ROOT` overrides the location for both setup and recognition;
`--root` changes only the install destination, so set the environment variable
as well when using a custom location. No administrator rights are required.
Existing Mac runtime.json files remain compatible. Re-running setup repairs
partial installations; runtime.json is published only after downloads complete.
If an installer was forcibly killed, remove its `install.lock` only after
checking that the installer has stopped. Close the application before repair.
The maintainer-only `--copy-runtime` option remains restricted to MLX.

Offline loading is forced during recognition. Model identities, actual backend,
device and dtype are recorded with job evidence. Windows Unicode filenames are
supported for crops and worker requests. An inference failure preserves the
primary OCR result and is explicitly reported.

## Terminal installation

The standard application installer includes the ONNX models and, on Windows,
the optional setup scripts. It does **not** include Qwen, GLM or their separate
runtime. Python 3.12 and internet access are needed for this one-time setup.
Close TableScan first.

### Installed Windows application — PowerShell

The default per-user installation can be set up interactively with:

```powershell
& "$env:LOCALAPPDATA\Programs\TableScan Local\tools\install-slow-mode.cmd"
```

This is the same script as the Start-menu shortcut and includes the model
self-test. If you chose another installation folder, substitute that path.

For an explicit device choice without the selection menu:

```powershell
$app = "$env:LOCALAPPDATA\Programs\TableScan Local"
py -3.12 "$app\tools\install_slow_mode.py" --backend transformers --device cpu
if ($LASTEXITCODE -ne 0) { throw "Slow mode installation failed" }
$check = Start-Process -FilePath "$app\TableScanLocal.exe" -ArgumentList "--slow-mode-self-test" -Wait -PassThru
if ($check.ExitCode -ne 0) { throw "Slow mode self-test failed; inspect the runtime self-test folder" }
```

Replace `cpu` with `auto` or `cuda` when needed. An explicit CUDA installation
requires a compatible NVIDIA GPU and driver; Auto can use CPU instead.

### Source checkout

Run from the repository root. Windows PowerShell:

```powershell
py -3.12 packaging/install_slow_mode.py --device cpu
if ($LASTEXITCODE -ne 0) { throw "Slow mode installation failed" }
.venv\Scripts\tablescan-local.exe --slow-mode-self-test
```

macOS or Linux:

```bash
python3.12 packaging/install_slow_mode.py && .venv/bin/tablescan-local --slow-mode-self-test
```

The main application must already be installed in `.venv` as described in the
[developer guide](development.md). The standalone Python installer downloads and
configures the runtime; the second command verifies both full models. Setup is
repeatable for repair, and does not enable Slow mode automatically in the UI.

## Verification

```bash
tablescan-local --self-test
tablescan-local --slow-mode-self-test
```

The second command exercises both full models on a synthetic image. The Windows
setup shortcut invokes it automatically. Results, model logs and synthetic crops
are kept under the runtime's `self-test` directory, including failures. No user
document is used. A successful synthetic test proves operation, not handwriting
accuracy. Windows uses unquantized Qwen weights whereas Mac uses MLX 4-bit Qwen;
identical accuracy across the two formats has not been assumed.

CI runs the desktop tests and packages on Windows/macOS/Linux. A separate Windows
job runs `packaging/smoke_torch_backend.py`: real multimodal CPU generation for
both architectures using tiny random weights and pinned real processors. This
checks CPU operations and integration without claiming OCR accuracy or downloading
full production models. Private validation documents are never uploaded to CI.


### Windows CPU instruction compatibility

The worker limits oneDNN to `AVX512_CORE_BF16` before loading PyTorch. This is
an upper limit, so processors with older instruction sets still use their
supported kernels. It avoids the AMX path that can crash on Windows virtual
machines advertising unavailable AMX instructions. BF16 weights and the models
are unchanged. A manually supplied stricter oneDNN ISA limit is preserved.

The related upstream investigation is [oneDNN #5689](https://github.com/uxlfoundation/oneDNN/issues/5689).
This mitigation is tested with native Windows CPU generation; full handwriting
accuracy and performance remain separate validation tasks.
