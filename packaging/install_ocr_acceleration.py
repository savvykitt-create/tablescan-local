"""Run after dependency installation, before tests/freezing a Windows build.

RapidOCR declares the CPU wheel as a dependency. Replace it after resolution;
installing both wheels together would overwrite the same onnxruntime module.
"""
import subprocess
import sys


def main():
    if sys.platform != "win32":
        return
    subprocess.run([sys.executable, "-m", "pip", "uninstall", "-y",
                    "onnxruntime", "onnxruntime-gpu", "onnxruntime-directml"], check=True)
    subprocess.run([sys.executable, "-m", "pip", "install", "onnxruntime-directml==1.24.4"], check=True)
    subprocess.run([sys.executable, "-c",
                    "import onnxruntime as ort; "
                    "assert 'DmlExecutionProvider' in ort.get_available_providers()"], check=True)


if __name__ == "__main__":
    main()
