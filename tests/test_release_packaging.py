import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def module(name, relative):
    path = Path(__file__).parents[1] / "packaging" / relative
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_license_inventory_contains_runtime_and_detects_missing_text(tmp_path):
    collector = module("collect_licenses", "collect_licenses.py")
    destination = collector.collect(tmp_path / "licenses")
    manifest = json.loads((destination / "manifest.json").read_text())
    names = {component["name"] for component in manifest["components"]}
    assert {"pyside6", "onnxruntime", "numpy", "pillow", "pypdfium2", "cpython", "rapidocr-onnxruntime"} <= names
    collector.verify(destination)
    first = manifest["components"][0]["files"][0]
    (destination / first["path"]).unlink()
    with pytest.raises(RuntimeError, match="Missing or changed"):
        collector.verify(destination)


def test_missing_reviewed_upstream_snapshot_fails_build(tmp_path, monkeypatch):
    collector = module("collect_licenses", "collect_licenses.py")
    monkeypatch.setattr(collector, "FALLBACKS", {})
    with pytest.raises(RuntimeError, match="No reviewed license"):
        collector.collect(tmp_path / "licenses")


@pytest.mark.parametrize("signature", ["Signature=adhoc\nTeamIdentifier=not set", "Authority=Developer ID Application: Test\nTeamIdentifier=TEST"])
def test_notarization_rejects_adhoc_or_missing_hardened_runtime_before_upload(tmp_path, monkeypatch, signature):
    notarizer = module("notarize", "macos/notarize.py")
    target = tmp_path / "Test.app"
    target.mkdir()
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(stderr=signature, stdout="")
    monkeypatch.setattr(notarizer.subprocess, "run", run)
    with pytest.raises(RuntimeError):
        notarizer.notarize(target, "profile")
    assert len(calls) == 1 and calls[0][0] == "codesign"


def test_notarization_requires_accepted_status_stapling_and_gatekeeper(tmp_path, monkeypatch):
    notarizer = module("notarize", "macos/notarize.py")
    target = tmp_path / "Test.app"
    target.mkdir()
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        assert kwargs.get("check")
        return SimpleNamespace(stderr="Authority=Developer ID Application: Test\nTeamIdentifier=TEST\nflags=runtime",
                               stdout=json.dumps({"status": "Accepted", "id": "test"}))
    monkeypatch.setattr(notarizer.subprocess, "run", run)
    notarizer.notarize(target, "profile")
    assert any(command[1:3] == ["stapler", "staple"] for command in calls)
    assert any(command[1:3] == ["stapler", "validate"] for command in calls)
    assert calls[-1][0] == "spctl"


def test_missing_signing_secrets_stops_release_before_keychain_mutations(monkeypatch):
    setup = module("setup_signing", "macos/setup_signing.py")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    for name in ("MACOS_CERTIFICATE_P12", "MACOS_CERTIFICATE_PASSWORD", "MACOS_SIGNING_IDENTITY", "APPLE_ID", "APPLE_TEAM_ID", "APPLE_APP_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(setup.sys, "argv", ["setup_signing.py"])
    monkeypatch.setattr(setup, "run", lambda *_: pytest.fail("Must not mutate keychains without credentials"))
    with pytest.raises(RuntimeError, match="needs repository secrets"):
        setup.main()
