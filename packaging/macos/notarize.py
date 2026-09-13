"""Notarize a signed app/DMG, staple its ticket and require Gatekeeper acceptance."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def notarize(target: Path, profile: str):
    signature = subprocess.run(["codesign", "-dv", "--verbose=4", str(target)],
                               check=True, capture_output=True, text=True).stderr
    if "Signature=adhoc" in signature or "TeamIdentifier=not set" in signature or "Authority=Developer ID Application:" not in signature:
        raise RuntimeError("Notarization requires a Developer ID Application signature")
    if target.suffix == ".app" and "runtime" not in signature:
        raise RuntimeError("The application must be signed with hardened runtime")
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(target)], check=True)
    with tempfile.TemporaryDirectory(prefix="tablescan-notary-") as temporary:
        submission = target
        if target.is_dir():
            submission = Path(temporary) / "application.zip"
            subprocess.run(["ditto", "-c", "-k", "--keepParent", str(target), str(submission)], check=True)
        command = ["xcrun", "notarytool", "submit", str(submission), "--keychain-profile", profile,
                   "--wait", "--output-format", "json"]
        if os.getenv("TABLESCAN_SIGNING_KEYCHAIN"):
            command.extend(["--keychain", os.environ["TABLESCAN_SIGNING_KEYCHAIN"]])
        response = json.loads(subprocess.run(command, check=True, capture_output=True, text=True).stdout)
        if response.get("status") != "Accepted":
            raise RuntimeError(f"Notarization was not accepted; submission {response.get('id', 'unknown')}")
    subprocess.run(["xcrun", "stapler", "staple", str(target)], check=True)
    subprocess.run(["xcrun", "stapler", "validate", str(target)], check=True)
    assessment = ["spctl", "--assess", "--verbose=4", "--type", "execute" if target.suffix == ".app" else "open"]
    if target.suffix == ".dmg":
        assessment.extend(["--context", "context:primary-signature"])
    subprocess.run([*assessment, str(target)], check=True)


if __name__ == "__main__":
    profile = os.getenv("TABLESCAN_NOTARY_PROFILE")
    if len(sys.argv) != 2 or not profile:
        raise SystemExit("Usage: TABLESCAN_NOTARY_PROFILE=<keychain-profile> python notarize.py <signed.app|signed.dmg>")
    notarize(Path(sys.argv[1]).resolve(), profile)
