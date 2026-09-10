# TableScan Local 0.7.4

- Workflow steps are enabled only when their prerequisites are met; Export opens the export flow.
- Template-editor rotation now works and records orientation for reopening the saved version. Rotation warns before clearing existing fields or cell rules.
- Field regions can be moved and resized using their edges and corners. Table boundary edits transform the existing grid proportionally, preserving unequal spacing. Guides are easier to grab and cannot cross adjacent guides.
- Rules and Fields panels scroll without compressing their controls. Column defaults are available inside Rules; the separate Columns tab is removed.
- Generated fields and the default measurement rule use English names. User-supplied saved names are preserved.
- Removed repeated local/offline messages and shortened template-match and rule-preview text.
- Non-modal corner notifications confirm template and field saves, rule application/deletion, grid detection, rotation, and export.

Validation: 140 tests passed, including pointer-driven field resizing, template rotation buttons, navigation prerequisites, and geometry serialization. Light and dark Rules layouts inspected at 1440 × 900.

Packaged macOS app: OCR self-test and strict recursive signature verification passed.
