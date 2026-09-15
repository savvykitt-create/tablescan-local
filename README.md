# TableScan Local

Turn scanned or photographed tables into editable Excel workbooks. Recognize
printed and handwritten values, check them against the original, and export the
results. Processing stays on your computer; no online account is required.

[Download](https://github.com/savvykitt-create/tablescan-local/releases/latest) ·
[Installation](#installation) · [Usage](#your-first-table) ·
[Development](docs/development.md)

## Installation

Download the file for your computer from the
[latest release](https://github.com/savvykitt-create/tablescan-local/releases/latest).
Expand **Assets** to see the installers.

| Computer | Download | Install |
| --- | --- | --- |
| Windows, 64-bit | `Setup-x64.exe` | Run the installer, then open **TableScan Local** from Start. |
| Mac with Apple Silicon | `macOS-arm64.dmg` | Open the disk image and copy **TableScan Local** to Applications. |
| Mac with Intel | `macOS-x86_64.dmg` | Open the disk image and copy **TableScan Local** to Applications. |
| Ubuntu, 64-bit Intel/AMD | `amd64.deb` | Install the package, then open **TableScan Local** from the application menu. |

The names above are the endings of the versioned filenames. On a Mac, **Apple
menu → About This Mac** identifies the chip or processor.

The installers include Python and the standard recognition models. A separate
Python installation and a graphics card are **not required for standard use**.
For a source installation, follow the [developer guide](docs/development.md).

### First launch on macOS

The default GitHub macOS build has an ad hoc signature, but is **not signed with
Apple Developer ID or notarized**. macOS may block its first launch because the
developer cannot be verified. This is an expected installation limitation.

If you trust the release downloaded from this repository:

1. Copy **TableScan Local** from the DMG to **Applications**, then try opening it.
2. If macOS blocks it, open **System Settings → Privacy & Security**.
3. Scroll to **Security**, find the message about TableScan Local and select
   **Open Anyway**. Authenticate and confirm opening when prompted.

This creates an exception for this application. See
[Apple's instructions](https://support.apple.com/en-ca/guide/mac-help/mh40616/mac).
Managed computers may prevent this exception. Developer ID signing and notarization
are an [optional release mode](docs/macos-release-signing.md).

## Your first table

1. **Files:** open a PDF, PNG, JPEG or TIFF. Multiple files and multipage PDFs
   are supported.
2. **Alignment:** choose a protocol and check the detected rotation, boundary,
   rows and columns. Adjust the grid if needed. Mark metadata outside the grid
   under **Fields** and configure expected values under **Rules**.
3. **Analyze:** choose Fast, High accuracy or Slow analysis. Standard recognition
   models are included; Slow analysis requires an additional installation.
4. **Review:** compare flagged values with their original crops. Edit and press
   **Enter** to confirm and continue, or **Alt+Right** to skip to the next disputed
   value. Handwriting suggestions appear below the main actions; **Alt+A** copies
   a suggestion into the editor for checking.
5. **Export:** after review, choose **Compact** for the original table layouts
   plus **Fields**, or **Extended** for layouts, normalized **Data** and **Audit**.

Rules guide recognition. Individual manual corrections override cell and field
rules, including numeric limits and decimal formats. Bulk confirmation still
leaves rule conflicts for individual review. **Exclude this row** and
**Exclude this column** omit measurements; clearing an exclusion restores them.

Choose English, Czech or Russian in **Settings → Language**. Document contents
and custom protocol names remain in the language in which you entered them.

## Process multiple files

Select or drop several files to open batch preparation. Each file automatically
selects its highest-scoring protocol. Compatibility describes layout matching,
not recognition accuracy or identification of the experiment.

Inspect the preview with its grid overlay before submitting. You can apply one
protocol to all files, select another per file, or open a file to correct its
rotation, grid, fields and rules. Uncertain fits require checking. The inspected
settings are saved separately for each analysis.

Analyses run sequentially. You can prepare another batch or review completed
results while processing continues. The **Analysis queue** shows progress,
preparation details and colored statuses. **×** removes a task and stops it if
running; saved results remain in file history. **Cancel all** stops unfinished
work, and **Resume all** restarts cancelled, interrupted or failed tasks from
the beginning. Tasks removed with **×** are not resumed.

Quitting the application interrupts unfinished analyses. They remain stopped
until you resume them. Closing only the queue window keeps processing running.

## Printable laboratory forms

**Von Frey, Plantar and Staircase** protocols are included. Their editable Excel
forms start with 20 animals, numbered IDs, distinct L/R header colors and a wide
Notes column. Insert or delete complete data rows before printing.

The protocol fits any detected animal count within the **200 total grid-row
limit**, including the header: with one header row, 1–199 animals. Counts such as
41 or 57 do not need a separate protocol. Keep column order and proportions,
uniform data-row heights and a consistent header. Scan quality must allow the
individual boundaries to be detected; check the fit preview.

Download [Excel forms](examples/lab_forms_excel/xlsx),
[protocol JSON files](examples/lab_forms_excel/templates), or the forms ZIP from
[Downloads](https://github.com/savvykitt-create/tablescan-local/releases/latest).
See the [printing guide](examples/lab_forms_excel/README.md). Pages with different
grids or animal counts must be analyzed as separate files.

## Recognition modes

| Mode | Use |
| --- | --- |
| Fast | Standard local OCR with fewer passes. |
| High accuracy | Additional OCR models, crop variations and candidate checks. |
| Slow | High accuracy followed by Qwen/GLM verification of disputed numeric measurements. |

Slow mode is optional. See [installation and troubleshooting](docs/slow-mode.md)
for Apple Silicon, Intel Mac, Windows and Linux. Its models download during setup
and subsequently run offline.

A slow-verification warning means that a model failed or its answer could not
be reliably mapped to rows and columns. The primary OCR result is preserved;
inspect the queue details and review the affected values. Model agreement and
confidence scores do not guarantee a correct handwritten reading.

For the recognition pipeline and model responsibilities, see the
[technical guide](docs/recognition.md). See [value rules](docs/VALUE_RULES.md)
for constraints and manual overrides.

## Files, updates and development

Original files are not modified. Imported copies, protocols, results and settings
are stored in the operating system's application-data directory. Installing an
updated application preserves this data and optional downloaded models.

- [Source installation, tests and packaging](docs/development.md)
- [Model sources and checksums](src/tablescan_local/models/README.md)
- [Third-party notices](THIRD_PARTY_NOTICES.md)

Application code is licensed under [Apache-2.0](LICENSE). Dependencies and model
weights retain their upstream licenses.
