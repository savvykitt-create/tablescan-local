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
does not load Metal libraries. Full requests, model responses, and crops remain
inside the local job's `slow-mode` directory.

## Install once

Requires an Apple Silicon Mac and Python 3.12. From the repository root:

```bash
python3.12 packaging/install_slow_mode.py
```

The installer downloads the optional dependencies and pinned model revisions.
Allow about 5 GB for weights plus dependency storage. No account is required.
Do not pass `--download` manually; it is the installer's internal second stage.
`--copy-runtime` is a maintainer option for copying an already validated MLX
virtual environment rather than installing its dependencies again.

The default installation is `~/Library/Application Support/TableScan Local/slow-mode`.
The application discovers the completed installation through `runtime.json`.
Keep this directory when updating the app. Source checkouts and packaged apps
use the same installation. A missing module is reported before recognition starts.
The module is not bundled in the normal downloadable installers.

- Qwen: `mlx-community/Qwen3.5-4B-MLX-4bit`, revision `32f3e8ecf65426fc3306969496342d504bfa13f3`.
- GLM: `mlx-community/GLM-OCR-bf16`, revision `24f15402e83baa0a80eeeaecf5480e172abc6f2e`.
- MLX 0.32.2, mlx-vlm 0.7.0; dependency pins are in the installer.

Recognition forces offline model loading. Internet is needed only for installation.
Other platforms support standard recognition; this MLX module is Apple Silicon only.

## Verification

The release passed 199 synthetic automated tests, standard packaged OCR self-test,
and an additional packaged self-test that exercises both external models and IPC:

```bash
tablescan-local --slow-mode-self-test
```

The latter requires the installed optional module and Metal access. Normal CI does
not download these models. Tests cover active rules, multiple pages, exclusions,
manual edits, response structure, cancellation, failure recovery, and mode toggles.
Private validation inputs and OCR run records are not distributed in the repository.
Accuracy and processing time depend on table layout, handwriting, and hardware;
allow several additional minutes for wide tables and keep reviewing disputed values.
