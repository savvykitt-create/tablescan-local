# TableScan Local 0.7.11

This update fixes stale template rules at recognition start and adds optional
local verification of disputed numeric cells.

## Changes since 0.7.6

- Recognition uses current editor settings; valid rule changes apply immediately.
  Saving a new template version makes it active. Run settings are saved as a draft
  before OCR and can be recovered after an interrupted run.
- Rule ranges follow resized grid edges. Resizing one axis preserves manual guides
  on the other. Typing a new row/column count no longer applies intermediate digits.
- Field text retains its original orientation and line order. Field highlighting
  clears when switching to Rules. Grid borders are handled more conservatively
  when detecting crossed-out rows.
- Numeric candidate ranking compares existing alternatives across independent
  models and image views. Uncertain changes remain available for review.
- Explicit numeric regions take precedence over generic header rows in Excel export.
- **Slow mode — additional verification** adds Qwen/GLM agreement checks on disputed
  measurements, with preserved exclusions, original readings, and review flags.
  Additional models run in isolated processes; failed verification preserves base OCR.

## Availability and update

Standard OCR supports Windows, macOS, and Ubuntu. **Slow mode requires an Apple
Silicon Mac and a separate, one-time module installation**; the standard installers
do not include its weights. See [setup instructions](slow-mode.md).

Close the application before replacing it. Keep the per-user application-data
folder to retain documents, templates, and preferences. Existing results are not
silently reprocessed; run recognition again to use the new candidate selection.

## Validation

199 automated tests passed locally on Apple Silicon. The macOS application passed
signature verification, standard OCR self-test, an additional test invoking both
Slow mode models from the packaged app, and visual inspection of the mode control.
Cross-platform CI verifies tests, packaging, and the standard bundled OCR runtime.
Handwriting remains probabilistic; important values still require source review.
