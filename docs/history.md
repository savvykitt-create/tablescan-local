# Earlier changes

Historical notes for versions 0.7.1–0.7.6. For current installation and usage,
see the [README](../README.md).

## Review workspace (0.7.1)

The lower preview now comes directly from the original page using the selected
cell or field coordinates. OCR masks and retry crops remain available to the
recognition pipeline but are never substituted for the source image in review.
Existing saved recognition results benefit immediately; rerunning OCR is not required.

- **Enter** in the value input confirms and advances; **Alt+Right** moves to the
  next disputed value without confirming. Navigation wraps across pages.
- **Alt+A** inserts a handwriting suggestion for inspection. Explicit confirmation
  is still required before it changes the result.
- **Why review is needed** expands grouped reasons, value rules, alternative
  readings, and technical checks. It is collapsed initially.
- Excluding a row blanks its measurements; clearing the checkbox restores them.
- The original document supports panning, zoom buttons, and **Fit**.
- Both export modes require completed review and retain the same value-rule checks.
  Compact export preserves the grid and blank excluded measurements on one sheet
  per page. Extended export retains the existing Data and Audit sheets.

The application icon uses the supplied SavvyKit logo. Source documents, OCR
models, and recognition policy are unaffected by this interface update.

## Interface languages (0.7.2)

English is the default for installations without a saved language choice,
regardless of the operating system language. Settings → Language offers
English, Čeština, and Русский. Switching language updates existing controls,
dialogs, tooltips, and review explanations without rebuilding the workspace.
The choice is stored alongside the theme in the local preferences file.

The review selection, entered value, template edits, and document images are
preserved. Document text, custom names, and existing audit evidence are data:
they are not translated. Generated names become ordinary text when they are
created. Numeric parsing and the review/export checks are identical in all
three languages. Source-layout worksheet titles use the selected language;
Data and Audit retain their established names and column schema.

Contributor notes: [Localization](LOCALIZATION.md).

## Digit segmentation (0.7.3)

The independent digit verifier and page-local handwriting comparison now preserve
complete digit groups when clear whitespace separates them. This avoids cutting
a wide glyph in half when it follows or precedes a narrow glyph, such as a handwritten
1. Small detached marks do not count as complete digit groups. The existing fallback
remains available when whitespace does not establish the expected segmentation.

There are no fixed substitutions between 1/7/2 or 4/9/6. Handwriting suggestions
still require explicit confirmation.

For an isolated first-run check, set `TABLESCAN_DATA_DIR` to an empty temporary
directory before launching the application. Reinstalling the source code alone
does not erase the existing per-user document database or preferences.

## Template editor and automatic numeric recovery (0.7.6)

- Workflow steps enable only when their prerequisites are met; Export opens the export flow.
- Rotate works in the template editor. Fields and table boundaries can be resized directly on the image, and grid spacing follows boundary changes.
- Rules contains column defaults; the separate Columns tab is removed. Fields and Rules scroll without compressing controls.
- Corner notifications confirm completed actions, and repeated explanatory labels have been removed.
- Missing decimal separators are recovered automatically when the numeric format specifies their placement. Inferred or ambiguous readings still require review.
- New template, field, column, rule, and copy names use English in every interface language. Existing saved names are preserved.
