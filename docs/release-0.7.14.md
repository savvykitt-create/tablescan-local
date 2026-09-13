# 0.7.14 — release audit fixes

This version addresses the reproducible defects found in the 0.7.13 release audit.
macOS releases use ad hoc signing by default; Developer
ID signing and notarization are optional. The first-launch Gatekeeper exception is
documented in the README. A local build alone does not validate installation of the
exact downloaded release artifact.

- Failed imports and malformed saved drafts preserve the entire active document.
  Unfinished imports are cleaned up, and legacy imported jobs can resume alignment.
- OCR, review and export share the effective header/explicit-region rules. Text
  headers can be corrected individually. Required/numeric fields are validated at
  confirmation and again at export, independent of stale OCR flags.
- Excel preserves identifier strings and leading zeros, and uses the declared or
  original decimal scale in both the original-layout sheets and normalized data.
  Numeric OCR hints for digit-only identifiers are retained.
- Excel exports are written and validated in a temporary sibling file before atomic
  replacement; failed writes leave the previous workbook intact.
- Advanced rule creation applies the coordinates accepted in its dialog.
- Working alignment settings autosave and flush on close. Save failures block a
  document switch/close instead of discarding work. Reviewed results remain available
  when closing without changing the template.
- JPEG import applies all EXIF orientations, including mirrored variants.
- History has search and pagination across all jobs, with local timestamps.
- Packages include full project/dependency licenses, exact versions and hashes.
  Missing wheel licenses use version-matched upstream snapshots. Unused Qt PDF
  image and Virtual Keyboard plugins are excluded before dependency collection.
- Release CI uses build constraints and immutable Action revisions. Publication is
  a separate job after all platform checks. The default macOS release needs no Apple
  credentials. Opting into `sign_macos` requires credentials and successful signing,
  notarization, stapling and Gatekeeper assessment; failures do not silently downgrade
  that requested signed release to ad hoc signing.
- Intel macOS uses ONNX Runtime 1.23.2, the latest published Python 3.12 wheel for
  that architecture; the other release platforms use 1.30.0.
- Windows export uses a writable descriptor when flushing the temporary workbook.
  Upstream license snapshots retain their exact bytes across Git checkouts on all
  platforms, so Windows line-ending conversion cannot invalidate their hashes.
- Linux package names and Debian metadata take their version from the application.
  Release publication rejects mismatched filenames and supplies portable checksums
  covering installers for every platform.

Regression tests cover the audit scenarios, invalid saved state, failed writes,
all eight EXIF orientations and access to the oldest of 101 jobs. macOS source and
frozen application checks are run locally. Windows/Linux GUI and installation of the
downloaded macOS DMG must still be checked on their actual release artifacts. Apple
notarization is checked only when the optional signed release mode is selected.

The Windows full-model CPU test can still report `FULL_CPU_NOT_RUN` on an undersized
runner. Tiny architecture tests are not evidence of full-model execution.
