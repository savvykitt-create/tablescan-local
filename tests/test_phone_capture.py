from contextlib import contextmanager
import hashlib
from http.client import HTTPConnection
from io import BytesIO
import json
from pathlib import Path
import socket
import time
from uuid import uuid4

import cv2
import numpy as np
from PIL import Image
import pytest

from tablescan_local import phone_capture as phone
from tablescan_local.storage import LocalStore


@contextmanager
def receiver(root):
    service = phone.PhoneReceiver(root, '127.0.0.1', {'phone': 'Phone'})
    try:
        yield service
    finally:
        service.stop()


def request(service, method='GET', path='/api/status', data=None, headers=None, authenticated=True):
    connection = HTTPConnection('127.0.0.1', service.server.server_port, timeout=5)
    sent = {'Authorization': f'Bearer {service.token}'} if authenticated else {}
    sent.update(headers or {})
    try:
        connection.request(method, path, body=data, headers=sent)
        response = connection.getresponse()
        raw = response.read()
        value = json.loads(raw) if response.getheader('Content-Type', '').startswith('application/json') else raw
        return response.status, value, dict(response.getheaders())
    finally:
        connection.close()


def image_bytes(format='JPEG'):
    output = BytesIO()
    Image.new('RGB', (32, 48), (170, 40, 30)).save(output, format=format)
    return output.getvalue()


@pytest.mark.parametrize('format,suffix', [('JPEG', '.jpg'), ('PNG', '.png'), ('TIFF', '.tiff'), ('WEBP', '.webp'), ('HEIF', '.heic')])
def test_receives_actual_format_preserves_original_and_commits_before_ack(tmp_path, format, suffix):
    data = ((Path(phone.__file__).parent / 'assets/heic-smoke.heic').read_bytes()
            if format == 'HEIF' else image_bytes(format))
    with receiver(tmp_path) as service:
        upload = uuid4().hex
        status, result, _ = request(service, 'PUT', '/api/photos/' + upload, data,
                                    {'Content-Type': 'text/plain', 'X-Filename': '../../untrusted.exe'})
        assert status == 200 and result['saved'] is True
        store = LocalStore(tmp_path)
        try:
            jobs = store.recent_jobs()
            assert len(jobs) == 1
            job_id, copied = store.phone_receipt(upload, hashlib.sha256(data).hexdigest())
            assert copied.suffix == suffix and copied.read_bytes() == data
            assert copied.parent.parent == store.jobs_dir
            metadata, _ = store.load_job(job_id)
            assert Path(metadata['source_path']) == copied
            assert service.events.get_nowait() == (job_id, copied.name)
            assert not list(tmp_path.glob('.phone-*'))
        finally:
            store.connection.close()


def test_lost_ack_retry_survives_receiver_restart_without_duplicate(tmp_path):
    upload, data = uuid4().hex, image_bytes()
    with receiver(tmp_path) as service:
        first = request(service, 'PUT', '/api/photos/' + upload, data)
    with receiver(tmp_path) as service:
        second = request(service, 'PUT', '/api/photos/' + upload, data)
        assert second[:2] == first[:2]
        assert service.received == 0 and service.events.empty()
        assert request(service, 'PUT', '/api/photos/' + upload, image_bytes('PNG'))[0] == 422
    store = LocalStore(tmp_path)
    assert store.job_count() == 1
    store.connection.close()


@pytest.mark.parametrize('headers,authenticated,status', [
    ({}, False, 401), ({'Authorization': 'Bearer wrong'}, True, 401),
    ({'Authorization': 'Bearer é'}, True, 401),
    ({'Host': 'attacker.invalid'}, True, 400),
    ({'Origin': 'https://attacker.invalid'}, True, 403),
])
def test_rejects_unauthorized_or_cross_origin_requests(tmp_path, headers, authenticated, status):
    with receiver(tmp_path) as service:
        assert request(service, headers=headers, authenticated=authenticated)[0] == status
        assert request(service, 'PUT', '/api/photos/' + uuid4().hex, image_bytes(), headers, authenticated)[0] == status
        assert service.received == 0


def test_expiration_rotation_and_stop_revoke_access(tmp_path):
    with receiver(tmp_path) as first:
        old_token = first.token
        port = first.server.server_port
        first.deadline = time.monotonic() - 1
        assert request(first)[0] == 410
        assert request(first, 'PUT', '/api/photos/' + uuid4().hex, image_bytes())[0] == 410
    with pytest.raises(OSError):
        socket.create_connection(('127.0.0.1', port), timeout=1)
    with receiver(tmp_path) as second:
        assert request(second, headers={'Authorization': 'Bearer ' + old_token})[0] == 401


def test_assets_are_local_and_server_never_serves_documents(tmp_path):
    (tmp_path / 'secret.txt').write_text('private')
    with receiver(tmp_path) as service:
        for path in ('/', '/camera.js', '/camera.css', '/copy.json'):
            status, body, headers = request(service, path=path, authenticated=False)
            assert status == 200 and body
            assert headers['Cache-Control'] == 'no-store'
            assert 'Access-Control-Allow-Origin' not in headers
        for path in ('/../secret.txt', '/%2e%2e/secret.txt', '/tablescan.db', '/api/photos', '/jobs/one/photo.jpg'):
            assert request(service, path=path)[0] == 404
        assert request(service, 'POST', '/api/photos/' + uuid4().hex, b'form')[0] == 501


@pytest.mark.parametrize('data', [b'not a photo', b'%PDF-1.7\n', image_bytes()[:80]])
def test_invalid_or_truncated_photo_never_creates_job(tmp_path, data):
    with receiver(tmp_path) as service:
        status = request(service, 'PUT', '/api/photos/' + uuid4().hex, data)[0]
        assert status == 422
        assert service.events.empty()
        assert not list((tmp_path / 'jobs').iterdir())
        assert not list(tmp_path.glob('.phone-*'))


def test_bounded_upload_and_session_limits(tmp_path, monkeypatch):
    with receiver(tmp_path) as service:
        path = '/api/photos/' + uuid4().hex
        assert request(service, 'PUT', path, b'', {'Content-Length': str(phone.MAX_PHOTO_BYTES + 1)})[0] == 413
        assert request(service, 'PUT', path, b'', {'Content-Length': '9' * 5000})[0] == 400
        service.received = phone.MAX_SESSION_PHOTOS
        assert request(service, 'PUT', path, image_bytes())[1]['error'] == 'session_full'
        service.received = 0
        monkeypatch.setattr(phone, 'MAX_PIXELS', 100)
        assert request(service, 'PUT', path, image_bytes())[0] == 413
        assert service.events.empty()


def test_partial_upload_stop_cleans_staging_and_does_not_create_job(tmp_path):
    with receiver(tmp_path) as service:
        client = socket.create_connection(('127.0.0.1', service.server.server_port), timeout=5)
        client.sendall((f'PUT /api/photos/{uuid4().hex} HTTP/1.1\r\nHost: 127.0.0.1:{service.server.server_port}\r\n'
                        f'Authorization: Bearer {service.token}\r\nContent-Length: 400000\r\n\r\n').encode() + b'partial')
        deadline = time.monotonic() + 3
        while not list(tmp_path.glob('.phone-*')) and time.monotonic() < deadline:
            time.sleep(.01)
        assert list(tmp_path.glob('.phone-*'))
        assert request(service, 'PUT', '/api/photos/' + uuid4().hex, image_bytes())[0] == 429
        service.stop()
        client.close()
        deadline = time.monotonic() + 3
        while list(tmp_path.glob('.phone-*')) and time.monotonic() < deadline:
            time.sleep(.01)
        assert not list(tmp_path.glob('.phone-*'))
        assert service.events.empty()


def test_database_failure_rolls_back_photo_and_receipt(tmp_path):
    store = LocalStore(tmp_path)
    store.connection.execute("CREATE TRIGGER fail_phone BEFORE INSERT ON phone_uploads BEGIN SELECT RAISE(ABORT, 'test failure'); END")
    store.connection.commit()
    with receiver(tmp_path) as service:
        assert request(service, 'PUT', '/api/photos/' + uuid4().hex, image_bytes())[1]['error'] == 'save_failed'
        assert service.events.empty()
    assert store.job_count() == 0
    assert not list(store.jobs_dir.iterdir())
    store.connection.close()


def test_qr_round_trip_including_fragment():
    url = 'http://192.168.123.123:54321/#' + 'aB9_' * 10 + 'Abc'
    matrix = np.array(phone.qr_matrix(url), dtype=np.uint8)
    pixels = np.repeat(np.repeat((1 - matrix) * 255, 6, axis=0), 6, axis=1)
    assert cv2.QRCodeDetector().detectAndDecode(pixels)[0] == url


def test_new_store_schema_keeps_existing_documents_and_deletion_removes_receipt(tmp_path):
    store = LocalStore(tmp_path)
    photo = tmp_path / 'sample.jpg'; photo.write_bytes(image_bytes())
    job_id, _ = store.import_source(photo, phone_receipt=('a' * 32, 'digest'))
    store.connection.close()
    reopened = LocalStore(tmp_path)
    assert reopened.job_count() == 1
    reopened.delete_job(job_id)
    assert reopened.phone_receipt('a' * 32, 'digest') is None
    assert reopened.connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    reopened.connection.close()
