import hashlib
import io
import json
from pathlib import Path
import stat
import sys
import subprocess
from unittest.mock import Mock
import zipfile

import pytest

from tablescan_local import app_update as update


def release_data():
    name = 'TableScan-Local-1.0.6-macOS-arm64.zip'
    return {'tag_name': 'v1.0.6', 'draft': False, 'prerelease': False, 'assets': [{
        'name': name, 'size': 3, 'digest': 'sha256:' + hashlib.sha256(b'zip').hexdigest(),
        'browser_download_url': f'https://github.com/{update.REPOSITORY}/releases/download/v1.0.6/{name}'}]}


def release():
    return update.parse_release(release_data(), '1.0.5', 'darwin', 'arm64')


def test_release_selection_and_no_downgrade():
    assert release().version == '1.0.6'
    assert update.parse_release(release_data(), '1.0.6', 'darwin', 'arm64') is None
    assert update.parse_release(release_data(), '1.1.0', 'darwin', 'arm64') is None
    assert update.asset_name('1.0.6', 'win32', 'AMD64').endswith('Setup-x64.exe')
    with pytest.raises(RuntimeError): update.asset_name('1.0.6', 'darwin', 'x86_64')


@pytest.mark.parametrize('field,value', [('browser_download_url','https://example.com/app.zip'),
                                         ('digest',''), ('size',0), ('size',True)])
def test_untrusted_asset_metadata_is_rejected(field, value):
    data = release_data(); data['assets'][0][field] = value
    with pytest.raises(ValueError): update.parse_release(data, '1.0.5', 'darwin', 'arm64')


def test_prerelease_and_invalid_versions_rejected():
    data = release_data(); data['prerelease'] = True
    with pytest.raises(ValueError): update.parse_release(data, '1.0.5')
    with pytest.raises(ValueError): update.version_tuple('v1.2.3/../../../app')


def test_checksum_verified_and_incomplete_downloads_removed(tmp_path, monkeypatch):
    monkeypatch.setattr(update, 'open_url', lambda url: io.BytesIO(b'zip'))
    path = update.download(release(), tmp_path)
    assert path.read_bytes() == b'zip'
    path.unlink()
    monkeypatch.setattr(update, 'open_url', lambda url: io.BytesIO(b'bad'))
    with pytest.raises(ValueError, match='checksum'): update.download(release(), tmp_path)
    assert not list(tmp_path.iterdir())
    with pytest.raises(update.UpdateCancelled): update.download(release(), tmp_path, cancelled=lambda: True)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('path', ['../other', '/Applications/evil', 'TableScan Local.app/../../escape', 'TableScan Local.app/..\\escape'])
def test_zip_paths_cannot_escape_staging(tmp_path, path):
    target = tmp_path/'update.zip'
    with zipfile.ZipFile(target, 'w') as archive: archive.writestr(path, 'content')
    with pytest.raises(ValueError): update.validate_archive(target)


def test_framework_links_allowed_but_escaping_links_rejected(tmp_path):
    target = tmp_path/'update.zip'
    def write(link):
        with zipfile.ZipFile(target, 'w') as archive:
            item=zipfile.ZipInfo('TableScan Local.app/Contents/Frameworks/Qt.framework/Versions/Current')
            item.create_system=3; item.external_attr=(stat.S_IFLNK|0o777)<<16
            archive.writestr(item,link)
    write('A'); update.validate_archive(target)
    write('../../../../../../outside')
    with pytest.raises(ValueError): update.validate_archive(target)


def test_reject_non_github_redirect():
    import urllib.request
    request=urllib.request.Request(update.API)
    for destination in ('http://github.com/x', 'https://example.com/x', 'https://github.com@evil.com/x'):
        with pytest.raises(ValueError): update.SafeRedirect().redirect_request(request,None,302,'',{},destination)


@pytest.mark.skipif(sys.platform == 'win32', reason='macOS/POSIX helper')
def test_mac_helper_preserves_old_app_and_handles_spaces(tmp_path):
    # Exercise the actual waiting/rename/rollback script without opening a GUI.
    target=tmp_path/'TableScan Local.app'; target.mkdir(); (target/'old').write_text('old')
    staging=tmp_path/'staging folder'; staging.mkdir()
    (staging/'TableScan Local.app').mkdir(); (staging/'TableScan Local.app/new').write_text('new')
    helper=tmp_path/'install.sh'
    helper.write_text(update.MAC_INSTALL.replace('/usr/bin/open', '/usr/bin/true'))
    subprocess.run(['/bin/sh',str(helper),'99999999',str(target),str(staging)],check=True)
    assert (target/'new').read_text()=='new'
    assert (staging/'previous.app/old').read_text()=='old'


def test_windows_installer_receives_paths_as_arguments(monkeypatch, tmp_path):
    monkeypatch.setattr(update.sys, 'platform', 'win32')
    run=Mock(); monkeypatch.setattr(update.subprocess, 'Popen', run)
    plan=update.PreparedUpdate(release(),tmp_path,tmp_path/'setup.exe',tmp_path/'app folder')
    update.launch_installer(plan)
    args=run.call_args.args[0]
    assert '/TABLESCANUPDATE=1' in args and f'/DIR={plan.target}' in args
    assert 'shell' not in run.call_args.kwargs


@pytest.mark.skipif(sys.platform == 'win32', reason='macOS/POSIX helper')
@pytest.mark.parametrize('missing_bundle', [True, False])
def test_mac_failed_replacement_or_launch_restores_previous_app(tmp_path, missing_bundle):
    target = tmp_path / 'TableScan Local.app'; target.mkdir()
    (target / 'old').write_text('previous version')
    staging = tmp_path / 'staging'; staging.mkdir()
    if not missing_bundle:
        (staging / 'TableScan Local.app').mkdir()
    helper = tmp_path / 'install.sh'
    helper.write_text(update.MAC_INSTALL.replace('/usr/bin/open', '/usr/bin/false'))
    result = subprocess.run(['/bin/sh', str(helper), '99999999', str(target), str(staging)])
    assert result.returncode != 0
    assert (target / 'old').read_text() == 'previous version'


def test_no_network_on_settings_creation(qtbot, monkeypatch):
    from tablescan_local.update_settings import UpdateSettings
    check=Mock(); monkeypatch.setattr(update,'check_latest',check)
    panel=UpdateSettings(); qtbot.addWidget(panel)
    check.assert_not_called()
    panel.completed(release())
    assert panel.release.version=='1.0.6'
    assert not panel.action.isHidden()


def test_update_action_waits_for_analysis(qtbot, monkeypatch):
    from tablescan_local.update_settings import UpdateSettings
    panel=UpdateSettings(busy=lambda: True); qtbot.addWidget(panel)
    panel.release=release()
    prepare=Mock(); monkeypatch.setattr(update,'prepare',prepare)
    panel.perform_action()
    prepare.assert_not_called()
    assert panel.worker is None


def test_background_notification_never_downloads_and_respects_preference(qtbot, monkeypatch, tmp_path):
    from PySide6.QtCore import QSettings
    from tablescan_local.update_settings import UpdateSettings
    preferences = QSettings(str(tmp_path/'preferences.ini'), QSettings.Format.IniFormat)
    check = Mock(return_value=release())
    prepare = Mock()
    monkeypatch.setattr(update, 'check_latest', check)
    monkeypatch.setattr(update, 'prepare', prepare)
    panel = UpdateSettings(preferences=preferences); qtbot.addWidget(panel)
    notices = []
    panel.updateAvailable.connect(notices.append)
    panel.start_automatic_checks()
    assert panel.check_timer.interval() == 10_000
    panel._automatic_check()
    qtbot.waitUntil(lambda: panel.worker is None)
    assert notices == [release()]
    assert panel.check_timer.interval() == 6 * 60 * 60 * 1000
    prepare.assert_not_called()
    panel.automatic.setChecked(False)
    assert not panel.check_timer.isActive()
    panel._automatic_check()
    assert check.call_count == 1
    other = UpdateSettings(preferences=preferences); qtbot.addWidget(other)
    other.start_automatic_checks()
    assert not other.check_timer.isActive()


def test_background_failure_preserves_known_update_and_can_retry(qtbot, monkeypatch):
    from tablescan_local.update_settings import UpdateSettings
    panel = UpdateSettings(); qtbot.addWidget(panel)
    panel.completed(release())
    notices = []
    panel.updateAvailable.connect(notices.append)
    monkeypatch.setattr(update, 'check_latest', Mock(side_effect=OSError('offline')))
    panel.check_updates(automatic=True)
    qtbot.waitUntil(lambda: panel.worker is None)
    assert panel.release == release() and not notices
    assert panel.check.isEnabled()
    monkeypatch.setattr(update, 'check_latest', lambda _: None)
    panel.check_updates(automatic=True)
    qtbot.waitUntil(lambda: panel.worker is None)
    assert notices == [None] and panel.release is None


def test_update_notice_is_visible_outside_settings_and_opens_updates(qtbot, monkeypatch, tmp_path):
    from tablescan_local.ui import create_application
    monkeypatch.setenv('TABLESCAN_DATA_DIR', str(tmp_path/'app-data'))
    _app, window = create_application(); qtbot.addWidget(window)
    assert window.update_notice.isHidden()
    window.update_settings.completed(release())
    assert not window.update_notice.isHidden()
    assert release().version in window.update_notice.text()
    window.update_notice.click()
    assert window.main_stack.currentIndex() == 2
    window.update_settings.completed(None)
    assert window.update_notice.isHidden()
