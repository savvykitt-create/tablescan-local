# 0.7.13 — Windows slow mode and CPU compatibility

Includes the cross-platform slow-mode implementation described in
[0.7.12](release-0.7.12.md), with a Windows CPU compatibility fix. The unpublished
0.7.12 draft is superseded: its final Windows CPU run failed with exception
`0xc000001d` inside Qwen's vision encoder. Earlier isolated runs had passed.

The worker now caps oneDNN dispatch below AMX before loading PyTorch, avoiding
an instruction path affected by incorrectly advertised Windows VM capabilities.
BF16 model storage is retained. The cap also applies when CUDA runs out of memory
and automatic mode retries on CPU. macOS MLX behavior is unchanged.

Repository ownership and README links now point to `savvykitt-create/tablescan-local`.
History, branches, tags and all existing release assets were preserved.

## Validation

See the release's linked CI run for final native Windows, macOS and Linux results.
The CPU integration probe generates tokens with both real model architectures,
pinned processors and tiny random weights, in three repetitions. A separate
full-weight synthetic probe runs only when runner RAM and disk meet its limits;
a skipped probe is explicitly reported as `FULL_CPU_NOT_RUN`.

Synthetic tests do not establish handwriting accuracy. No Windows handwritten
validation score or NVIDIA performance measurement is claimed by this release.
