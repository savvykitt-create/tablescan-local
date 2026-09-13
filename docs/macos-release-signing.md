# macOS releases and optional Developer ID signing

Public releases and local builds use **ad hoc signing by default**, with no Apple
account, certificate or repository secrets required. The first launch of a downloaded
app may require the Gatekeeper exception described in the [README](../README.md#first-launch-on-macos).
This is an accepted installation limitation for this project's GitHub distribution.

**Developer ID Application** signing and Apple notarization are optional. Enable
them when credentials are available and a smoother first launch is desired.
PyInstaller enables hardened runtime when given a signing identity;
the project does not add blanket entitlement exceptions. See
[PyInstaller signing support](https://pyinstaller.org/en/stable/feature-notes.html)
and [Apple Developer ID](https://developer.apple.com/help/account/certificates/create-developer-id-certificates).

## GitHub Actions

Leave the `sign_macos` workflow input unchecked (the default) to publish ad hoc
macOS packages. This skips signing-keychain setup and Apple notarization; package
tests, license checks and the publication dependencies still run.

To request a Developer ID release, check `sign_macos` and configure these repository
secrets through GitHub's encrypted secrets UI, never in
source files, issue comments or chat:

| Secret | Value |
|---|---|
| `MACOS_CERTIFICATE_P12` | Base64-encoded exported Developer ID Application certificate and private key |
| `MACOS_CERTIFICATE_PASSWORD` | Password of that P12 export |
| `MACOS_SIGNING_IDENTITY` | Full `Developer ID Application: … (TEAMID)` identity |
| `APPLE_ID` | Apple account used for notarization |
| `APPLE_TEAM_ID` | Developer team identifier |
| `APPLE_APP_PASSWORD` | Apple app-specific password for notarization |

When `sign_macos` is enabled, the manually dispatched release workflow installs an ephemeral runner keychain,
imports the certificate, stores a notarytool profile, signs every collected binary,
notarizes/staples the app, and signs/notarizes/staples the DMG. It verifies Gatekeeper
acceptance before saving packages for publication. An `always()` cleanup step restores
the original keychain search list and removes the temporary signing keychain. Missing
credentials stop that requested signed release; it does not silently fall back to
ad hoc signing. With `sign_macos` disabled, these credentials are not needed.

Only the separate `publish` job has repository write permission. It waits for the
full build matrix and Windows CPU job. The workflow is not automatically dispatched
by preparing these changes; publishing still requires selecting the intended release.

## Local signing with existing credentials

Install the build dependencies with `packaging/constraints.txt`. With an identity
already present in your keychain, and a notarytool profile stored by the account owner:

```sh
export TABLESCAN_CODESIGN_IDENTITY='Developer ID Application: Your name (TEAMID)'
export TABLESCAN_NOTARY_PROFILE='your-existing-notary-profile'
export TABLESCAN_REQUIRE_SIGNED=1
pyinstaller --clean --noconfirm packaging/tablescan_local.spec
python packaging/verify_bundle.py dist
python packaging/macos/notarize.py 'dist/TableScan Local.app'
bash packaging/macos/make_dmg.sh 'dist/TableScan Local.app' 'TableScan-Local-0.7.14-macOS-arm64.dmg'
```

Use the corresponding architecture name for an Intel build. Do not use the CI
keychain helper on a personal computer; it deliberately only runs in GitHub Actions.

## Validate the downloaded release

For either mode, download the exact DMG from GitHub Releases through a browser onto
a clean Mac or a separate test machine. Copy the app into Applications and check
first launch, Documents-folder access, import, review, Excel export, restart and
opening existing 0.7.11 data. For an ad hoc release, verify the README's **Open Anyway**
steps work; for a Developer ID release, verify notarization and Gatekeeper acceptance.
A source test or local launch cannot establish the downloaded artifact's behavior.

## License inventory

`packaging/collect_licenses.py` uses the build environment and the reviewed snapshots
under `packaging/licenses`. It performs no network access during packaging. The exact
source URLs and hashes of those snapshots are in `packaging/licenses/sources.json`.
`packaging/vendor_licenses.py` is a separate maintainer command for refreshing them;
review the new source versions, attribution documents and license inventory before
changing constraints or fallback version mappings. Qt has component-specific licensing:
see [Qt licensing](https://doc.qt.io/qt-6/licensing.html) and
[Qt for Python notices](https://doc.qt.io/qtforpython-6/licenses.html).

The application contains `licenses/manifest.json`, full license texts, model provenance
and notices. `packaging/verify_bundle.py` checks these files and rejects the excluded
Qt plugins in the actual frozen output. Upstream snapshots include some notices for
components not shipped by this Widgets application; this preserves the upstream texts.
