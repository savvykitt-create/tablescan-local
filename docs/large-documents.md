# Large documents and recovery

PDF and multi-frame TIFF files are read one page at a time. Recognition uses the
same full-resolution pixels as before (PDF scale 3.0); the application does not
reduce OCR resolution to save memory. Opening, rotating and reviewing a document
uses a random-access page source instead of a list of every rendered page.

The ordinary image cache holds at most two pages and 64 MiB; a recognition worker
caches at most one page within the same byte limit. A page larger than the limit
is returned without being cached. The displayed page and native rendering buffers
also need memory. Batch preparation checks all four orientations of each page
before moving on, and renders preview thumbnails on demand without retaining
thumbnails for every file in the batch. Native document handles close after each
read. A changed or unreadable source is reported rather than combined with old
cached pixels.

## Continuing interrupted work

Completed **primary OCR pages** and **Slow verification pages** are saved in the
job's `analysis-checkpoint` directory, separately from the saved result and its
manual corrections. Each page stage is written to a temporary file, flushed and
atomically replaced. An interrupted or corrupt write does not make a partially
written page available for export.

- **Resume all** and retrying a failed task reuse completed stages of that queue
  submission. A process restart retains its run identity.
- A stage interrupted in the middle of a page runs again from the start of that
  stage. Individual OCR cells and individual Qwen/GLM calls are not checkpoints.
- Starting a fresh analysis creates a new run identity and recomputes the result.
- A completed Slow stage belongs to the exact saved primary page it verified.
  It cannot be attached to a different primary result.
- Missing, incompatible or damaged page checkpoints are recomputed. Missing
  source crops also prevent reuse of the affected checkpoint.
- Jobs remain stopped after a normal application exit until the user resumes
  them. Checkpointing does not change queue removal or cancellation controls.

Compatibility includes source contents, render settings, the fitted protocol,
recognition mode, application/implementation identity, model file contents,
runtime versions/configuration and the available OCR accelerator. File hashes
are streamed in small chunks. Unchanged model hashes are reused within the
process while their filesystem identity, size and modification/change times
remain the same. Checking optional model files may add startup time after an
application restart, but does not load their weights into memory.

All primary OCR pages finish before Slow verification starts; ONNX sessions and
cell workers are released before the larger Slow models are loaded. A completed
Slow stage can still have a `partial` or `failed` verification status: its primary
readings remain available and its warning remains visible. A fresh analysis is
required to rerun such a completed verification.

Diagnostics show the number of restored OCR/Slow pages. Queue duration and
`performance.total_seconds` describe the latest attempt. Reused primary pages
have zero current-attempt duration in `primary_pages_seconds`; their previous
stage times are recorded separately under `performance.checkpoint`. Restored
Slow evidence retains its original execution measurements and is labelled as
restored in the diagnostic view.

## Code boundaries

- `documents.py`: page access, rotation, cancellation and bounded raster cache.
- `pipeline.py`: cell/page recognition and the public document entry point.
- `document_analysis.py`: document stage ordering, progress and checkpoint reuse.
- `checkpoints.py`: input fingerprints, atomic writes and checkpoint validation.
- `recognition_worker.py`: Qt thread boundary and cancellation/error signals.
- `document_canvas.py` and `review_page.py`: source canvas and review widgets.
- `review_actions.py`: confirmation, exclusion and review order without Qt.
- `ui.py`: the application window, preparation controls and navigation.

`load_document()` remains an explicit eager compatibility helper for external
callers. Application flows use `open_document()`. List-based callers of
`process_document()` remain supported; checkpointing is opt-in for callers
outside the application's queue and requires a stable run identity.

## Remaining limits

Raster memory is bounded by a few pages, not total page count. Recognized cells,
document geometry, saved JSON results and Excel workbooks still grow with the
amount of data. This is not a constant-memory export engine. A single very large
page still requires enough memory to render and recognize it.

One protocol/grid is still shared across all pages of a job. Automatic fitting
checks every page and rejects incompatible grids. Pages with different layouts
must be prepared as separate files. The new reader does not change recognition
rules, model thresholds or the requirement to review disputed values.
