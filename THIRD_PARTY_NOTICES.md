# Third-party notices

TableScan Local depends on the following open-source projects. Release builds must include the exact license files distributed by the installed package versions.

- Qt for Python / PySide6 — LGPL-3.0-only, GPL-3.0-only, or commercial terms depending on component.
- RapidOCR ONNX Runtime — Apache-2.0.
- ONNX Runtime — MIT.
- OpenCV — Apache-2.0.
- PDFium and pypdfium2 — BSD-3-Clause / Apache-2.0 components.
- openpyxl — MIT.
- Pillow — HPND.
- NumPy — BSD-3-Clause.

Model weights shipped by `rapidocr-onnxruntime` remain subject to their upstream notices. Before a public release, the exact license files from the dependency versions used for that release must be reviewed and included with the installer.

Version 0.2 additionally bundles PaddleOCR PP-OCRv5 server and English mobile
recognizers distributed by RapidAI. Pinned download URLs and verified SHA-256
hashes are included in `tablescan_local/models/README.md` inside the application.
PaddleOCR and RapidOCR upstream projects use Apache-2.0 licensing.

Version 0.4 additionally bundles the official PaddlePaddle
`PP-OCRv6_medium_rec` ONNX model from the pinned Hugging Face revision recorded
in `tablescan_local/models/README.md`. Its model card declares Apache-2.0.

Version 0.5 additionally bundles a compact digit-verifier model trained by the
project on NIST EMNIST Digits. Dataset provenance and the exact model checksum
are recorded in `tablescan_local/models/README.md`. Training and validation
datasets are not included in the repository or release package.

The optional, separately installed slow-mode module supports MLX on Apple Silicon
and PyTorch/Transformers on Windows and other CPU platforms.
Its pinned Qwen3.5-4B MLX model card declares Apache-2.0; its GLM-OCR model card
declares MIT. The installer retains downloaded model cards and any upstream
LICENSE/NOTICE files. MLX and mlx-vlm use MIT licensing. Optional weights and
dependencies are not bundled in the standard application packages. See
`docs/slow-mode.md` and `packaging/install_slow_mode.py` for repositories and pins.

The Transformers backend downloads Qwen/Qwen3.5-4B (Apache-2.0) and
zai-org/GLM-OCR (MIT), at the revisions in `slow_runtime.py`. Its optional
PyTorch/torchvision dependencies use BSD-style licenses. Their distributions
and downloaded model directories contain the respective license notices.
