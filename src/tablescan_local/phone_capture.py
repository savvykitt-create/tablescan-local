"""Opt-in LAN photo receiver. No discovery, cloud service or remote file access."""
from __future__ import annotations

from datetime import datetime
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path
from queue import SimpleQueue
import re
import secrets
import shutil
import socket
import sqlite3
from tempfile import TemporaryDirectory
import threading
import time

from PIL import Image, UnidentifiedImageError
import psutil

from . import documents  # Registers the HEIF decoder before network imports.
from .storage import LocalStore

MAX_PHOTO_BYTES = 40 * 1024 * 1024
MAX_PIXELS = 80_000_000
MAX_SESSION_PHOTOS = 200
SESSION_SECONDS = 2 * 60 * 60
ASSETS = Path(__file__).parent / 'phone_web'
FORMATS = {'JPEG': '.jpg', 'PNG': '.png', 'HEIF': '.heic', 'TIFF': '.tiff', 'WEBP': '.webp'}


def local_addresses():
    """Offer usable private IPv4 interfaces without contacting an internet host."""
    states = psutil.net_if_stats()
    choices = []
    for name, addresses in psutil.net_if_addrs().items():
        if name not in states or not states[name].isup:
            continue
        for address in addresses:
            if address.family != socket.AF_INET:
                continue
            ip = ipaddress.ip_address(address.address)
            if ip.is_private and not (ip.is_loopback or ip.is_link_local or ip.is_unspecified):
                choices.append((name, str(ip)))
    return sorted(set(choices), key=lambda item: (item[0].lower().startswith(
        ('utun', 'tun', 'tap', 'tailscale', 'bridge', 'vbox', 'vmnet', 'vethernet')), item))


def qr_matrix(url):
    import qrcode
    code = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=4)
    code.add_data(url)
    code.make(fit=True)
    return code.get_matrix()


class PhotoTooLarge(ValueError):
    pass


def validate_photo(path):
    """Identify actual bytes, not a browser-supplied filename or MIME type."""
    try:
        with Image.open(path) as photo:
            suffix = FORMATS.get(photo.format)
            if not suffix or getattr(photo, 'n_frames', 1) > 8:
                raise ValueError('unsupported_photo')
            for index in range(getattr(photo, 'n_frames', 1)):
                photo.seek(index)
                if photo.width * photo.height > MAX_PIXELS:
                    raise PhotoTooLarge('photo_too_large')
                photo.load()  # Reject truncated data before acknowledging receipt.
    except OSError as exc:
        raise ValueError('invalid_photo') from exc
    return suffix


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    allow_reuse_address = False
    request_queue_size = 4

    def __init__(self, owner, address):
        self.owner = owner
        self.slots = threading.BoundedSemaphore(4)
        self.clients = set()
        self.clients_lock = threading.Lock()
        super().__init__(address, _Handler)

    def get_request(self):
        client, address = super().get_request()
        client.settimeout(20)
        return client, address

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        with self.clients_lock:
            self.clients.add(request)
        try:
            super().process_request(request, client_address)
        except BaseException:
            with self.clients_lock:
                self.clients.discard(request)
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            with self.clients_lock:
                self.clients.discard(request)
            self.slots.release()

    def handle_error(self, request, client_address):
        pass  # Never put authorization headers or local document paths in logs.


class PhoneReceiver:
    def __init__(self, root, host, texts, *, port=0, lifetime=SESSION_SECONDS):
        ip = ipaddress.ip_address(host)
        if ip.version != 4 or ip.is_unspecified or ip.is_multicast or not ip.is_private:
            raise ValueError('A private IPv4 address is required')
        self.root = Path(root)
        self.texts = {key: str(value) for key, value in texts.items()}
        self.token = secrets.token_urlsafe(32)
        self.deadline = time.monotonic() + lifetime
        self.stopped = threading.Event()
        self.upload_lock = threading.Lock()
        self.commit_lock = threading.Lock()
        self.events = SimpleQueue()
        self.last_seen = 0.0
        self.received = 0
        self.server = _Server(self, (host, port))
        self.origin = f'http://{host}:{self.server.server_port}'
        self.url = f'{self.origin}/#{self.token}'
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={'poll_interval': .1}, daemon=True)
        self.thread.start()

    @property
    def expired(self):
        return time.monotonic() >= self.deadline

    def stop(self):
        if self.stopped.is_set():
            return
        # A confirmed import and its UI event must finish together before stop.
        with self.commit_lock:
            self.stopped.set()
        with self.server.clients_lock:
            for client in tuple(self.server.clients):
                try:
                    client.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)


class _Handler(BaseHTTPRequestHandler):
    server_version = 'TableScan'
    sys_version = ''

    @property
    def owner(self):
        return self.server.owner

    def log_message(self, *args):
        pass

    def reply(self, status, payload, content_type='application/json; charset=utf-8'):
        if isinstance(payload, dict):
            payload = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' blob:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
        self.send_header('Connection', 'close')
        self.end_headers()
        self.close_connection = True
        try:
            self.wfile.write(payload)
        except OSError:
            pass

    def valid_host(self):
        host = self.headers.get_all('Host', [])
        if host != [self.owner.origin.removeprefix('http://')]:
            self.reply(400, {'error': 'invalid_link'})
            return False
        origins = self.headers.get_all('Origin', [])
        if origins and origins != [self.owner.origin]:
            self.reply(403, {'error': 'invalid_link'})
            return False
        return True

    def authorized(self):
        if not self.valid_host():
            return False
        if self.owner.stopped.is_set() or self.owner.expired:
            self.reply(410, {'error': 'session_ended'})
            return False
        expected = f'Bearer {self.owner.token}'.encode()
        authorization = self.headers.get_all('Authorization', [])
        if len(authorization) != 1 or not secrets.compare_digest(authorization[0].encode(), expected):
            self.reply(401, {'error': 'invalid_link'})
            return False
        self.owner.last_seen = time.monotonic()
        return True

    def do_GET(self):
        if self.path == '/api/status':
            if self.authorized():
                self.reply(200, {'connected': True, 'limit': MAX_PHOTO_BYTES})
            return
        if not self.valid_host():
            return
        if self.path == '/':
            self.reply(200, (ASSETS / 'index.html').read_bytes(), 'text/html; charset=utf-8')
        elif self.path == '/copy.json':
            self.reply(200, self.owner.texts)
        elif self.path in ('/camera.js', '/camera.css'):
            kind = 'text/javascript' if self.path.endswith('.js') else 'text/css'
            self.reply(200, (ASSETS / self.path[1:]).read_bytes(), kind + '; charset=utf-8')
        elif self.path == '/favicon.ico':
            self.reply(204, b'')
        else:
            self.reply(404, {'error': 'not_found'})

    def do_PUT(self):
        if not self.authorized():
            return
        match = re.fullmatch(r'/api/photos/([a-f0-9]{32})', self.path)
        lengths = self.headers.get_all('Content-Length', [])
        if (not match or self.headers.get('Transfer-Encoding') or len(lengths) != 1
                or len(lengths[0]) > 10 or not lengths[0].isascii() or not lengths[0].isdigit()):
            self.reply(400, {'error': 'invalid_photo'})
            return
        length = int(lengths[0])
        if not 0 < length <= MAX_PHOTO_BYTES:
            self.reply(413, {'error': 'photo_too_large'})
            return
        if not self.owner.upload_lock.acquire(blocking=False):
            self.reply(429, {'error': 'busy'})
            return
        try:
            result = self.receive_photo(match[1], length)
        except (socket.timeout, ConnectionError):
            result = 408, {'error': 'retry'}
        except (PhotoTooLarge, Image.DecompressionBombError):
            result = 413, {'error': 'photo_too_large'}
        except (UnidentifiedImageError, ValueError, EOFError):
            result = 422, {'error': 'invalid_photo'}
        except (OSError, sqlite3.Error):
            result = 503, {'error': 'save_failed'}
        finally:
            self.owner.upload_lock.release()
        # Release the upload slot and clean staging before the browser sends next.
        self.reply(*result)

    def receive_photo(self, upload_id, length):
        self.owner.root.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(self.owner.root).free < length * 2 + 128 * 1024 * 1024:
            return 507, {'error': 'disk_full'}
        # Staging is private to this request; only validated originals enter jobs.
        with TemporaryDirectory(prefix='.phone-', dir=self.owner.root) as folder:
            path = Path(folder) / 'upload'
            digest = hashlib.sha256()
            remaining = length
            started = time.monotonic()
            with path.open('wb') as stream:
                while remaining:
                    if self.owner.stopped.is_set() or self.owner.expired or time.monotonic() - started > 120:
                        return 410, {'error': 'session_ended'}
                    chunk = self.rfile.read1(min(256 * 1024, remaining))
                    if not chunk:
                        return 400, {'error': 'retry'}
                    stream.write(chunk)
                    digest.update(chunk)
                    remaining -= len(chunk)
            store = LocalStore(self.owner.root)
            try:
                previous = store.phone_receipt(upload_id, digest.hexdigest())
                if previous:
                    return 200, {'saved': True, 'name': previous[1].name}
                if self.owner.received >= MAX_SESSION_PHOTOS:
                    return 429, {'error': 'session_full'}
                suffix = validate_photo(path)
                photo = path.with_name(f'Phone-{datetime.now():%Y%m%d-%H%M%S}-{upload_id[:8]}{suffix}')
                path.rename(photo)
                with self.owner.commit_lock:
                    if self.owner.stopped.is_set() or self.owner.expired:
                        return 410, {'error': 'session_ended'}
                    job_id, copied = store.import_source(photo, phone_receipt=(upload_id, digest.hexdigest()))
                    self.owner.received += 1
                    self.owner.events.put((job_id, copied.name))
                return 200, {'saved': True, 'name': copied.name}
            finally:
                store.connection.close()
