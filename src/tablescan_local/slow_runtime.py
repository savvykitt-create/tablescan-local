"""Dependency-free runtime contract shared by app, installer and workers."""
import os
import platform
import sys
from pathlib import Path

MODEL_SETS = {
    "mlx": {
        "qwen": ("mlx-community/Qwen3.5-4B-MLX-4bit", "32f3e8ecf65426fc3306969496342d504bfa13f3"),
        "glm": ("mlx-community/GLM-OCR-bf16", "24f15402e83baa0a80eeeaecf5480e172abc6f2e"),
    },
    "transformers": {
        "qwen": ("Qwen/Qwen3.5-4B", "851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a"),
        "glm": ("zai-org/GLM-OCR", "2e85a62840ccac27daa451df36c736c4636b8628"),
    },
}


def default_backend():
    return "mlx" if sys.platform == "darwin" and platform.machine() == "arm64" else "transformers"


def runtime_root():
    override = os.environ.get("TABLESCAN_SLOW_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "TableScan Local/slow-mode"
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/TableScan Local/slow-mode"
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "tablescan-local/slow-mode"


def venv_python(folder):
    return Path(folder) / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
