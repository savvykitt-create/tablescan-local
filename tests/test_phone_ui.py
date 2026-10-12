import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from http.client import HTTPConnection
from io import BytesIO
import json
from uuid import uuid4

from PIL import Image

from tablescan_local.ui import create_application
from tablescan_local import phone_page
from tablescan_local.domain import TableTemplate, NormalizedRect


def test_phone_import_preserves_active_document_opens_received_and_stops_on_close(tmp_path, monkeypatch, qtbot):
    monkeypatch.setenv('TABLESCAN_DATA_DIR', str(tmp_path / 'data'))
    monkeypatch.setattr(phone_page, 'local_addresses', lambda: [('Test', '127.0.0.1')])
    _, window = create_application()
    qtbot.addWidget(window)
    page = window.phone_page
    assert page.receiver is None
    active = tmp_path / 'active.png'; Image.new('RGB', (100, 100), 'white').save(active)
    template = TableTemplate('test', 'Test', NormalizedRect(0, 0, 1, 1), [0, 1], [0, 1])
    template.ensure_column_rules()
    window.open_source(str(active), reused_template=template)
    original_id = window.job_id
    page.start()
    service = page.receiver
    assert service is not None and page.link.text() == service.url
    assert not page.qr.pixmap().isNull()
    data = BytesIO(); Image.new('RGB', (100, 100), 'white').save(data, format='PNG')
    connection = HTTPConnection('127.0.0.1', service.server.server_port, timeout=5)
    connection.request('PUT', '/api/photos/' + uuid4().hex, data.getvalue(), {'Authorization': 'Bearer ' + service.token})
    response = connection.getresponse()
    assert response.status == 200 and json.loads(response.read())['saved']
    connection.close()
    qtbot.waitUntil(lambda: page.photos.count() == 1)
    assert window.job_id == original_id
    assert window.store.job_count() == 2
    window._navigate_section(3)
    page.photos.setCurrentRow(0)
    page.open_button.click()
    assert window.job_id != original_id
    assert window.main_stack.currentIndex() == 0
    window.close()
    assert service.stopped.is_set() and page.receiver is None


def test_missing_network_and_expiry_are_recoverable(tmp_path, monkeypatch, qtbot):
    monkeypatch.setattr(phone_page, 'local_addresses', lambda: [])
    page = phone_page.PhonePage(tmp_path)
    qtbot.addWidget(page)
    assert not page.start_button.isEnabled()
    monkeypatch.setattr(phone_page, 'local_addresses', lambda: [('Test', '127.0.0.1')])
    page.refresh_networks()
    page.start()
    first = page.receiver
    first.deadline = 0
    page.poll()
    assert page.receiver is None and first.stopped.is_set() and not page.link.text()
    page.start()
    assert page.receiver.token != first.token
    page.stop()
