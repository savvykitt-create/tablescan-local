from __future__ import annotations

import sys

import cv2
import numpy as np

from .ui import create_application, InstanceAlreadyRunning


def document_self_test() -> int:
    """Exercise the actual bundled HEIF decoder, frame order and orientation."""
    from pathlib import Path
    from .documents import open_document
    pages = open_document(Path(__file__).parent / 'assets/heic-smoke.heic')
    if len(pages) != 2 or pages[0].shape != (96, 64, 3) or pages[1].shape != (64, 96, 3):
        raise RuntimeError('Packaged HEIC size, orientation or frame order is wrong')
    for actual, expected in ((pages[0][10, 10], (30, 20, 230)),
                             (pages[0][-10, -10], (230, 30, 20)),
                             (pages[1][10, 10], (50, 180, 30))):
        if np.max(np.abs(actual.astype(int) - expected)) > 4:
            raise RuntimeError('Packaged HEIC pixels or orientation are wrong')
    return 0


def phone_self_test() -> int:
    """Exercise bundled QR generation, web assets, HTTP upload and retry receipts."""
    from http.client import HTTPConnection
    import json
    from pathlib import Path
    from tempfile import TemporaryDirectory
    from uuid import uuid4
    from .phone_capture import PhoneReceiver, qr_matrix
    from .phone_page import mobile_texts
    from .storage import LocalStore
    with TemporaryDirectory(prefix='tablescan-phone-selftest-') as folder:
        receiver = PhoneReceiver(Path(folder), '127.0.0.1', mobile_texts())
        try:
            matrix = np.array(qr_matrix(receiver.url), dtype=np.uint8)
            pixels = np.repeat(np.repeat((1 - matrix) * 255, 6, axis=0), 6, axis=1)
            if cv2.QRCodeDetector().detectAndDecode(pixels)[0] != receiver.url:
                raise RuntimeError('Packaged phone QR code could not be read')
            upload = uuid4().hex
            photo = (Path(__file__).parent / 'assets/heic-smoke.heic').read_bytes()
            cases = [('GET', path, None) for path in ('/', '/camera.css', '/camera.js', '/copy.json', '/api/status')]
            cases += [('PUT', '/api/photos/' + upload, photo)] * 2
            for method, path, body in cases:
                client = HTTPConnection('127.0.0.1', receiver.server.server_port, timeout=10)
                try:
                    client.request(method, path, body, {'Authorization': 'Bearer ' + receiver.token})
                    response = client.getresponse()
                    payload = response.read()
                    if response.status != 200 or not payload:
                        raise RuntimeError('Packaged phone transfer failed')
                    if method == 'PUT' and not json.loads(payload).get('saved'):
                        raise RuntimeError('Packaged phone receipt missing')
                finally:
                    client.close()
            store = LocalStore(Path(folder))
            try:
                jobs = store.recent_jobs()
                if len(jobs) != 1 or Path(jobs[0]['stored_source_path']).read_bytes() != photo:
                    raise RuntimeError('Packaged phone original or duplicate protection failed')
            finally:
                store.connection.close()
        finally:
            receiver.stop()
    return 0


def self_test(*, parallel=False) -> int:
    """Load the packaged OCR runtime and run one in-memory recognition pass."""
    document_self_test()
    phone_self_test()
    from .slow_setup import download_context
    if not download_context().get_ca_certs():
        raise RuntimeError("Python download certificates are missing from the application")
    from .ocr import LocalOcrEngine, OcrValue, constrain_reading
    from .constraints import ValueConstraints

    image = np.full((96, 320, 3), 255, dtype=np.uint8)
    cv2.putText(image, "123.4", (18, 68), cv2.FONT_HERSHEY_SIMPLEX, 1.8, (0, 0, 0), 3, cv2.LINE_AA)
    result = LocalOcrEngine().recognize_cell(image, numeric=True)
    if result.text != "123.4" or "numeric_verification_required" not in result.flags:
        raise RuntimeError(f"Packaged OCR self-test failed: {result.text!r}")
    precise = LocalOcrEngine(high_accuracy=True).recognize_cell(
        image, numeric=True,
        constraints=ValueConstraints("numeric", minimum=0, maximum=200, decimal_places=1),
    )
    if precise.text != "123.4" or "high_accuracy_consensus" not in precise.flags:
        raise RuntimeError(f"Packaged high-accuracy OCR self-test failed: {precise.text!r}")
    recovered = constrain_reading(
        OcrValue("3417", .99),
        ValueConstraints("numeric", minimum=0, maximum=60, decimal_places=1, suggest_missing_decimal=True, require_decimal=True),
    )
    if recovered.text != "34.7" or "separator_reclassified_from_one" not in recovered.flags:
        raise RuntimeError(f"Template decimal self-test failed: {recovered.text!r}")
    if parallel:
        from .parallel_ocr import CellWorkers
        from .numeric_decoder import _native_search
        if _native_search is None:
            raise RuntimeError('Native decoder missing from performance build')
        tasks = [(i, dict(crop=image, numeric=True, constraints=ValueConstraints(
            'numeric', minimum=0, maximum=200, decimal_places=1))) for i in range(2)]
        pool = CellWorkers(2)
        try:
            results = list(pool.map(tasks, LocalOcrEngine(high_accuracy=True)))
            if pool.completed == 0:
                raise RuntimeError('Packaged CPU worker did not complete a cell')
            adaptive_args = dict(tasks[0][1], adaptive=True)
            direct = LocalOcrEngine(high_accuracy=True).recognize_cell(**adaptive_args)
            parallel_adaptive = list(pool.map([(0, adaptive_args)], LocalOcrEngine(high_accuracy=True)))[0][1]
            if direct.text != parallel_adaptive.text or direct.flags != parallel_adaptive.flags:
                raise RuntimeError('Packaged adaptive worker parity failed')
            if not {'adaptive_short_pass', 'adaptive_full_pass'}.intersection(direct.flags):
                raise RuntimeError('Packaged adaptive route was not executed')
            if [key for key, _ in results] != [0, 1] or any(
                value.text != precise.text or value.flags != precise.flags for _, value in results
            ):
                raise RuntimeError('Packaged parallel OCR parity failed')
        finally:
            pool.close()
    return 0


def slow_mode_self_test() -> int:
    """Exercise both external models and packaged IPC without a GUI or user data."""
    import json
    import tempfile
    import shutil
    from datetime import datetime, timezone
    from pathlib import Path
    from .domain import CellResult, PageResult, TableTemplate, NormalizedRect, CellRuleRegion
    from .constraints import ValueConstraints
    from .slow_mode import runtime_config, refine_page
    from .slow_runtime import runtime_root
    image = np.full((100, 600, 3), 255, np.uint8)
    for x, value in [(20, '12.3'), (320, '45.6')]:
        cv2.putText(image, value, (x, 70), cv2.FONT_HERSHEY_SIMPLEX, 1.8, (0, 0, 0), 3, cv2.LINE_AA)
    template = TableTemplate('self-test', 'Self-test', NormalizedRect(0, 0, 1, 1), [0, 1], [0, .5, 1])
    template.row_label_columns = 0
    template.ensure_column_rules()
    template.cell_rules = [CellRuleRegion('values', 'Values', 0, 0, 0, 1,
        ValueConstraints('numeric', minimum=0, maximum=70, decimal_places=1))]
    page = PageResult(0, 'synthetic', [CellResult(0, c, '99.9', '99.9', .5, flags=['model_disagreement']) for c in range(2)], [])
    report = runtime_root() / 'self-test'
    report.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='tablescan-slow-selftest-') as directory:
        try:
            refine_page(image, page, template, Path(directory), runtime_config())
            if page.slow_mode.get('status') != 'complete' or [c.final_text for c in page.cells] != ['12.3', '45.6']:
                raise RuntimeError(f'Slow-mode self-test failed: {page.slow_mode}')
            summary = {'status': 'passed', 'slow_mode': page.slow_mode}
        except Exception as exc:
            summary = {'status': 'failed', 'error': str(exc), 'slow_mode': page.slow_mode}
        finally:
            shutil.copytree(directory, report, dirs_exist_ok=True)
        summary['timestamp'] = datetime.now(timezone.utc).isoformat()
        (report / 'result.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
        print(json.dumps(summary))
        if summary['status'] != 'passed':
            return 1
    return 0


def main() -> int:
    if "--uninstall-data" in sys.argv:
        from .cleanup import uninstall_main
        return uninstall_main()
    if "--slow-mode-self-test" in sys.argv:
        return slow_mode_self_test()
    if "--phone-self-test" in sys.argv:
        return phone_self_test()
    if "--document-self-test" in sys.argv:
        return document_self_test()
    if "--self-test" in sys.argv:
        return self_test()
    if "--parallel-self-test" in sys.argv:
        return self_test(parallel=True)
    try:
        app, window = create_application(lock_store=True)
    except InstanceAlreadyRunning as exc:
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.warning(None, 'TableScan Local', str(exc))
        return 1
    window.show()
    window.update_settings.start_automatic_checks()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
