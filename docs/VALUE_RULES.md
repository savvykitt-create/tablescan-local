# Value rules

## Configure recognition

In Alignment, check the grid and header-row count before opening **Rules**.
Select cells on the image, a row, a column, or all measurements. Set a value type,
allowed limits, decimal places and whether blank values are permitted. Bounds
are inclusive; leave a bound empty to remove it. Allowed-value lists use
semicolons so decimal commas remain unambiguous.

Rules are stored with the protocol. A cell-region rule overrides a column rule;
smaller regions take priority. Equally specific overlapping regions with
conflicting constraints must be resolved before analysis. Header and ignored
cells retain their roles unless explicitly covered by a cell rule.

| Setting | Recognition behavior |
| --- | --- |
| Integer, numeric, text or complex notation | Selects the reading format. |
| Decimal places | Checks decimal structure. |
| Allowed minimum/maximum | Flags candidates outside inclusive limits. |
| Expected range | Flags unusual values without making them invalid. |
| Prefer expected range | Prefers an observed eligible candidate; preserves alternatives for review. |
| Allowed values | Restricts candidate selection to listed values. |
| Suggest missing decimal | May suggest insertion between observed digits; requires review. |

Rules do not train models, invent digits or establish ground truth. For example,
both 47.2 and 97.2 may satisfy the same numeric range. The image remains the
reference for deciding between them.

## Manual review and export

Individual **Confirm and continue** accepts the value entered by the reviewer,
even when it violates the protocol's range, value type, required-value setting
or decimal format. Manual cell and field corrections are authoritative and are
not silently normalized to fit a rule. Export preserves these overrides and their
numeric precision. Audit retains the original reading and applied rule.

**Confirm all disputed** only accepts values that already satisfy the rules;
conflicts remain for deliberate individual review. Unreviewed conflicts still
block export. Excluded measurements are omitted until their exclusion is cleared.

Changing a protocol does not reinterpret previously confirmed results. Prepare
and analyze the source again to apply revised recognition rules.
