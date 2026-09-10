# Interface localization

The default locale is `en`. Supported languages are `en`, `cs`, and `ru`.
`appearance/language` in the existing preferences file stores the choice.
No locale preference is inferred from the system language.

`src/tablescan_local/translations/messages.json` holds complete translations
for each message. Legacy Russian source strings are stable message identifiers;
additional messages can use English identifiers. Every key must provide all
three translations with the same format fields and formatting specifications.

Use `tr(source, **values)` for application-owned messages. Use `fmt()` for
compositions of translated labels and literal data, `join_text()` for lists,
and `escape_text()` for rich-text escaping. Their `Text` values retain a render
recipe so existing controls can be updated when the locale changes.

Text-bearing widgets imported from `localized_widgets` bind only these explicit
`Text` values. Plain strings containing document values or custom names are
never matched against the catalog. Line edits drop their text binding when the
user edits them. Qt signals are blocked during retranslation so changing a
language cannot trigger document edits, OCR, export, or selection callbacks.
Weak references prevent the binding registry from retaining discarded widgets.

Freeze translated default names with `str()` at the point where they enter a
template, field, or rule. Machine identifiers, OCR flags, model scores, paths,
and numeric values must not become translated identifiers. Rule descriptions
are rendered from structured constraints for the active language; saved audit
evidence is retained in its original form.

Qt standard dialogs use the bundled `qtbase_cs.qm` and `qtbase_ru.qm` from the
installed PySide6 distribution. File dialogs use Qt rather than native dialogs
so their language follows the app selection. English uses Qt's source texts.
These catalogs are packaged in both the Python distribution and the macOS app.

`tests/test_i18n.py` verifies catalog completeness, format fields, source coverage,
English default, persistence, live switching, unchanged document/input state,
open dialogs, generated names, and locale-independent numeric/export behavior.
