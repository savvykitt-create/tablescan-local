"""Explicit, checksum-verified updates from the project's GitHub releases."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import plistlib
import posixpath
import re
import shutil
import ssl
import stat
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile

import certifi

REPOSITORY = 'savvykitt-create/tablescan-local'
API = f'https://api.github.com/repos/{REPOSITORY}/releases/latest'
MAX_DOWNLOAD = 1024**3


class UpdateCancelled(Exception):
    pass


def version_tuple(value):
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)', value)
    if not match:
        raise ValueError('Unsupported release version')
    return tuple(map(int, match.groups()))


def asset_name(version, system=None, machine=None):
    system, machine = system or sys.platform, (machine or platform.machine()).lower()
    if system == 'darwin' and machine in {'arm64', 'aarch64'}:
        return f'TableScan-Local-{version}-macOS-arm64.zip'
    if system == 'win32' and machine in {'amd64', 'x86_64'}:
        return f'TableScan-Local-{version}-Setup-x64.exe'
    raise RuntimeError('Automatic updates support Apple Silicon macOS and Windows x64.')


@dataclass(frozen=True)
class Release:
    version: str
    url: str
    sha256: str
    size: int
    name: str


def parse_release(data, current, system=None, machine=None):
    if data.get('draft') or data.get('prerelease'):
        raise ValueError('Only stable published releases may be installed')
    tag = data['tag_name']
    if version_tuple(tag) <= version_tuple(current):
        return None
    version = tag.removeprefix('v')
    name = asset_name(version, system, machine)
    asset = next((a for a in data.get('assets', []) if a.get('name') == name), None)
    if asset is None:
        raise RuntimeError('The latest release has no package for this computer.')
    url = asset['browser_download_url']
    expected = f'https://github.com/{REPOSITORY}/releases/download/{tag}/{name}'
    if url != expected:
        raise ValueError('Unexpected release asset URL')
    digest = asset.get('digest', '')
    if not re.fullmatch(r'sha256:[a-fA-F0-9]{64}', digest):
        raise ValueError('The release asset has no valid SHA-256 digest')
    size = asset['size']
    if type(size) is not int or not 0 < size <= MAX_DOWNLOAD:
        raise ValueError('Invalid release asset size')
    return Release(version, url, digest[7:].lower(), size, name)


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        url = urllib.parse.urlparse(newurl)
        if (url.scheme != 'https' or url.username or url.password or url.port not in (None, 443)
                or url.hostname not in {'github.com', 'api.github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com'}):
            raise ValueError('Unexpected update download redirect')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_url(url):
    opener = urllib.request.build_opener(SafeRedirect(), urllib.request.HTTPSHandler(
        context=ssl.create_default_context(cafile=certifi.where())))
    return opener.open(urllib.request.Request(url, headers={'User-Agent': 'TableScan-Local-Updater',
                                                           'Accept': 'application/vnd.github+json' if url == API else 'application/octet-stream'}), timeout=30)


def check_latest(current):
    with open_url(API) as response:
        raw = response.read(2 * 1024**2 + 1)
    if len(raw) > 2 * 1024**2:
        raise ValueError('Release metadata is too large')
    return parse_release(json.loads(raw), current)


def download(release, folder, progress=lambda done, total: None, cancelled=lambda: False):
    target = Path(folder) / release.name
    partial = target.with_suffix(target.suffix + '.part')
    digest = hashlib.sha256()
    count = 0
    try:
        with open_url(release.url) as response, partial.open('wb') as output:
            while True:
                if cancelled():
                    raise UpdateCancelled()
                chunk = response.read(1024**2)
                if not chunk:
                    break
                count += len(chunk)
                if count > release.size:
                    raise ValueError('Update download exceeds its advertised size')
                output.write(chunk); digest.update(chunk)
                progress(count, release.size)
        if count != release.size or digest.hexdigest() != release.sha256:
            raise ValueError('Update download checksum or size mismatch')
        partial.replace(target)
        return target
    finally:
        partial.unlink(missing_ok=True)


def validate_archive(path):
    """Reject traversal and escaping links before ditto preserves framework links."""
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > 30000 or sum(item.file_size for item in entries) > 3 * 1024**3:
            raise ValueError('Update archive is too large')
        for item in entries:
            name = PurePosixPath(item.filename)
            if ('\\' in item.filename or name.is_absolute() or '..' in name.parts
                    or not name.parts or name.parts[0] not in {'TableScan Local.app', '__MACOSX'}):
                raise ValueError('Unsafe update archive path')
            if stat.S_ISLNK(item.external_attr >> 16):
                link = archive.read(item).decode('utf-8')
                resolved = posixpath.normpath(posixpath.join(posixpath.dirname(item.filename), link))
                if link.startswith('/') or '\\' in link or not resolved.startswith('TableScan Local.app/'):
                    raise ValueError('Unsafe update archive link')


def installed_target():
    if not getattr(sys, 'frozen', False):
        raise RuntimeError('Install a packaged TableScan release to use automatic updates.')
    exe = Path(sys.executable).resolve()
    if sys.platform == 'darwin' and exe.parent.name == 'MacOS' and exe.parent.parent.name == 'Contents':
        return exe.parent.parent.parent
    if sys.platform == 'win32' and exe.name.lower() == 'tablescanlocal.exe':
        return exe.parent
    raise RuntimeError('The current installation location is not supported.')


@dataclass
class PreparedUpdate:
    release: Release
    folder: Path
    package: Path
    target: Path
    staging: Path | None = None

    def cleanup(self):
        if self.staging:
            shutil.rmtree(self.staging, ignore_errors=True)
        shutil.rmtree(self.folder, ignore_errors=True)


def prepare(release, progress=lambda done, total: None, cancelled=lambda: False):
    target = installed_target()
    folder = Path(tempfile.mkdtemp(prefix='tablescan-update-'))
    plan = PreparedUpdate(release, folder, folder / release.name, target)
    try:
        plan.package = download(release, folder, progress, cancelled)
        if sys.platform == 'darwin':
            validate_archive(plan.package)
            # Stage on the installation volume so replacement/rollback are renames.
            plan.staging = Path(tempfile.mkdtemp(prefix='.tablescan-update-', dir=target.parent))
            subprocess.run(['/usr/bin/ditto', '-x', '-k', str(plan.package), str(plan.staging)], check=True, timeout=120)
            app = plan.staging / 'TableScan Local.app'
            with (app / 'Contents/Info.plist').open('rb') as source:
                info = plistlib.load(source)
            if info.get('CFBundleShortVersionString') != release.version or info.get('CFBundleIdentifier') != 'org.tablescan.local':
                raise ValueError('Update bundle identity or version mismatch')
            subprocess.run([str(app / 'Contents/MacOS/TableScanLocal'), '--self-test'],
                           check=True, timeout=120, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if cancelled():
            raise UpdateCancelled()
        return plan
    except BaseException:
        plan.cleanup()
        raise


# No remote text is evaluated. Paths are positional arguments, never shell code.
MAC_INSTALL = '''#!/bin/sh
set -eu
pid="$1"
target="$2"
staging="$3"
count=0
while kill -0 "$pid" 2>/dev/null; do
    count=$((count + 1))
    [ "$count" -lt 120 ] || exit 1
    sleep 1
done
backup="$staging/previous.app"
test -d "$target"
/bin/mv "$target" "$backup"
if ! /bin/mv "$staging/TableScan Local.app" "$target"; then
    /bin/mv "$backup" "$target"
    exit 1
fi
if ! /usr/bin/open "$target"; then
    /bin/mv "$target" "$staging/failed.app"
    /bin/mv "$backup" "$target"
    /usr/bin/open "$target"
    exit 1
fi
'''


def launch_installer(plan):
    """Caller must save work and close after dispatch; never invokes uninstall."""
    if sys.platform == 'darwin':
        helper = plan.folder / 'install.sh'
        helper.write_text(MAC_INSTALL, encoding='utf-8')
        with (plan.folder / 'install.log').open('ab') as log:
            subprocess.Popen(['/bin/sh', str(helper), str(os.getpid()), str(plan.target), str(plan.staging)],
                             stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    elif sys.platform == 'win32':
        subprocess.Popen([str(plan.package), '/SP-', '/SILENT', '/NORESTART', '/CLOSEAPPLICATIONS',
                          '/NORESTARTAPPLICATIONS', '/TABLESCANUPDATE=1', f'/DIR={plan.target}'], close_fds=True)
    else:
        raise RuntimeError('Unsupported update platform')
