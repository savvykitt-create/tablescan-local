"""Configure an ephemeral signing keychain on a GitHub-hosted macOS runner."""
import base64
import json
import os
from pathlib import Path
import secrets
import shlex
import subprocess
import sys
import tempfile


def run(*command):
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        # Some commands contain passwords: never include their argv in errors.
        raise RuntimeError(f"macOS signing setup failed in {command[0]}")
    return result.stdout


def main():
    if os.getenv("GITHUB_ACTIONS") != "true":
        raise RuntimeError("This helper is only for disposable GitHub Actions runners")
    if "--cleanup" in sys.argv:
        state_path = os.getenv("TABLESCAN_SIGNING_STATE")
        if state_path and Path(state_path).exists():
            state = json.loads(Path(state_path).read_text())
            run("security", "list-keychains", "-d", "user", "-s", *state["original_keychains"])
            if Path(state["keychain"]).exists():
                run("security", "delete-keychain", state["keychain"])
            Path(state_path).unlink()
        return
    required = ("MACOS_CERTIFICATE_P12", "MACOS_CERTIFICATE_PASSWORD", "MACOS_SIGNING_IDENTITY",
                "APPLE_ID", "APPLE_TEAM_ID", "APPLE_APP_PASSWORD")
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError("Signed macOS release needs repository secrets: " + ", ".join(missing))
    identity = os.environ["MACOS_SIGNING_IDENTITY"]
    if not identity.startswith("Developer ID Application:") or any(c in identity for c in "\r\n"):
        raise RuntimeError("MACOS_SIGNING_IDENTITY must be a Developer ID Application identity")
    folder = Path(tempfile.mkdtemp(prefix="tablescan-signing-", dir=os.environ["RUNNER_TEMP"]))
    keychain = folder / "release.keychain-db"
    state_path = folder / "state.json"
    original = shlex.split(run("security", "list-keychains", "-d", "user"))
    state_path.write_text(json.dumps({"keychain": str(keychain), "original_keychains": original}))
    with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as environment:
        environment.write(f"TABLESCAN_SIGNING_STATE={state_path}\nTABLESCAN_SIGNING_KEYCHAIN={keychain}\n"
                          f"TABLESCAN_CODESIGN_IDENTITY={identity}\nTABLESCAN_REQUIRE_SIGNED=1\n"
                          "TABLESCAN_NOTARY_PROFILE=tablescan-release\n")
    password = secrets.token_hex(32)
    certificate = folder / "certificate.p12"
    try:
        certificate.write_bytes(base64.b64decode(os.environ["MACOS_CERTIFICATE_P12"], validate=True))
        certificate.chmod(0o600)
        run("security", "create-keychain", "-p", password, str(keychain))
        run("security", "set-keychain-settings", "-lut", "21600", str(keychain))
        run("security", "unlock-keychain", "-p", password, str(keychain))
        run("security", "import", str(certificate), "-P", os.environ["MACOS_CERTIFICATE_PASSWORD"],
            "-k", str(keychain), "-T", "/usr/bin/codesign", "-T", "/usr/bin/security")
        run("security", "set-key-partition-list", "-S", "apple-tool:,apple:,codesign:", "-s", "-k", password, str(keychain))
        run("security", "list-keychains", "-d", "user", "-s", str(keychain), *original)
        run("xcrun", "notarytool", "store-credentials", "tablescan-release", "--keychain", str(keychain),
            "--apple-id", os.environ["APPLE_ID"], "--team-id", os.environ["APPLE_TEAM_ID"],
            "--password", os.environ["APPLE_APP_PASSWORD"])
    finally:
        certificate.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
