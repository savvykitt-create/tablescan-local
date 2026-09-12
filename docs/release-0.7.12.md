# 0.7.12 — Windows slow mode

Windows gains the same Qwen/GLM disagreement verification policy as macOS,
with a separate Transformers runtime supporting CPU and NVIDIA CUDA. Standard
OCR, Maximum accuracy, cancellation detection, writer adaptation, rules,
templates, review and Excel export share the existing implementation.

- CPU uses BF16 weights; float32 is an optional compatibility mode.
- Auto chooses a device per model and retries CUDA out-of-memory on CPU.
- The Windows Start-menu setup shortcut installs/repairs the optional module and
  runs a full-model synthetic self-test. Python 3.12 is required.
- Failed setup is not marked ready. Workers retain baseline OCR on inference
  failure. Cancellation stops the active worker.
- External Python is isolated from the frozen application's DLL search path.
- Crops, writer adaptation and worker files support Unicode Windows paths.
- Existing Apple Silicon MLX runtime installations remain compatible.
- Self-test diagnostics persist under the optional runtime's `self-test` folder.

## Validation scope

Native Windows CI passed all 213 desktop tests and compiled the x64 installer.
The dedicated Windows CPU job passed real multimodal generation with both
architectures, pinned processors and tiny random weights. The full existing MLX
self-test passed on Apple Silicon and preserved its two correct control values.
Additional configuration-corruption regressions and repeated CPU probes are
included in the final CI run. Test logs and the final validation scope are linked
from the GitHub release. These checks prove integration, not handwriting accuracy.

The Windows Qwen weights are BF16, whereas the existing MLX Qwen is 4-bit.
The Mac handwriting accuracy figures must not be advertised as Windows results
without evaluating the Windows backend on the annotated dataset. CUDA performance
also requires measurements on a physical NVIDIA system.
