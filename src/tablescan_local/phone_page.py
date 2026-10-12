"""Phone pairing and received-photo history; the active document is untouched."""
from pathlib import Path
import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import QApplication, QHBoxLayout, QVBoxLayout

from .i18n import language, tr
from .localized_widgets import QWidget, QLabel, QPushButton, QComboBox, QLineEdit, QListWidget, QListWidgetItem
from .phone_capture import PhoneReceiver, local_addresses, qr_matrix
from .theme import set_theme_style
from .ui_common import MUTED


def mobile_texts():
    return {
        'language': language(), 'phone': tr('Phone'),
        'connecting': tr('Connecting…'), 'connected': tr('Connected to your computer'),
        'disconnected': tr('Connection unavailable'), 'title': tr('Scan with your phone'),
        'intro': tr('Take a photo. It will appear in TableScan on your computer.'),
        'take_photo': tr('Take a photo'),
        'camera_hint': tr('Confirm the photo in your camera to send it. You can take the next page afterwards.'),
        'reconnect': tr('Reconnect'), 'photos': tr('Your photos'),
        'empty': tr('Your sent photos will appear here.'),
        'privacy': tr('Direct transfer to your computer. Keep this page open until every photo is saved.'),
        'queued': tr('Waiting to send'), 'sending': tr('Sending…'), 'saved': tr('Saved on computer'),
        'failed': tr('Not sent'), 'photo': tr('Photo'), 'retry_button': tr('Retry'),
        'retry': tr('Not confirmed. Tap Retry; an already saved photo will not be duplicated.'),
        'invalid_link': tr('Open a new connection in TableScan and scan its QR code again.'),
        'session_ended': tr('This connection has ended. Start a new one in TableScan.'),
        'connection_help': tr('Keep TableScan open and connect both devices to the same Wi-Fi network. Then tap Reconnect.'),
        'invalid_photo': tr('This image could not be read. Take another photo or choose a supported image.'),
        'photo_too_large': tr('Choose a photo smaller than 40 MB and 80 megapixels.'),
        'busy': tr('Another photo is being saved. Tap Retry in a moment.'),
        'save_failed': tr('The computer could not save this photo. Check its storage and tap Retry.'),
        'disk_full': tr('The computer needs more free disk space. Free some space and tap Retry.'),
        'session_full': tr('This connection has received 200 photos. Start a new connection for more.'),
        'queue_full': tr('Send or retry the waiting photos before taking more.'),
    }


def qr_pixmap(url):
    matrix = qr_matrix(url)
    scale = max(3, 280 // len(matrix))
    image = QImage(len(matrix) * scale, len(matrix) * scale, QImage.Format.Format_RGB32)
    image.fill(QColor('white'))
    from PySide6.QtGui import QPainter
    painter = QPainter(image)
    for y, row in enumerate(matrix):
        for x, value in enumerate(row):
            if value:
                painter.fillRect(x * scale, y * scale, scale, scale, QColor('black'))
    painter.end()
    return QPixmap.fromImage(image)


class PhonePage(QWidget):
    photoImported = Signal()
    openRequested = Signal(str)

    def __init__(self, root: Path, parent=None):
        super().__init__(parent)
        self.root = root
        self.receiver = None
        self._received_ids = set()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 30, 36, 30)
        layout.setSpacing(18)
        title = QLabel(tr('Phone'))
        set_theme_style(title, 'font-size: 25px; font-weight: 750;')
        layout.addWidget(title)
        description = QLabel(tr('Scan the QR code with your phone, take photos, and find them in Documents. No mobile app is required.'))
        description.setWordWrap(True)
        layout.addWidget(description)
        controls = QHBoxLayout()
        self.network = QComboBox()
        self.network.setAccessibleName(tr('Local network'))
        self.refresh_button = QPushButton(tr('Refresh networks'))
        self.refresh_button.clicked.connect(self.refresh_networks)
        self.start_button = QPushButton(tr('Connect phone'))
        self.start_button.setProperty('primary', True)
        self.start_button.clicked.connect(self.start)
        self.stop_button = QPushButton(tr('Disconnect phone'))
        self.stop_button.clicked.connect(self.stop)
        self.stop_button.hide()
        for widget in (self.network, self.refresh_button, self.start_button, self.stop_button):
            controls.addWidget(widget)
        controls.addStretch()
        layout.addLayout(controls)
        self.status = QLabel(tr('Phone connection is off.'))
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.qr = QLabel()
        self.qr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr.setAccessibleName(tr('Scan this QR code with your phone camera'))
        self.qr.hide()
        layout.addWidget(self.qr, alignment=Qt.AlignmentFlag.AlignLeft)
        link_row = QHBoxLayout()
        self.link = QLineEdit()
        self.link.setReadOnly(True)
        self.link.setAccessibleName(tr('Phone connection link'))
        self.link.hide()
        self.copy_button = QPushButton(tr('Copy link'))
        self.copy_button.clicked.connect(lambda: QApplication.clipboard().setText(self.link.text()))
        self.copy_button.hide()
        link_row.addWidget(self.link)
        link_row.addWidget(self.copy_button)
        layout.addLayout(link_row)
        note = QLabel(tr('Use the same trusted Wi-Fi network on both devices. Photos travel directly over your local network without encryption. Keep TableScan open; each connection lasts up to 2 hours.'))
        note.setWordWrap(True)
        set_theme_style(note, f'color: {MUTED};')
        layout.addWidget(note)
        help_text = QLabel(tr('If the phone cannot connect, select another local network above, leave guest Wi-Fi, or allow TableScan through your firewall on a private network.'))
        help_text.setWordWrap(True)
        set_theme_style(help_text, f'color: {MUTED};')
        layout.addWidget(help_text)
        self.count = QLabel(tr('Received photos: {count}', count=0))
        layout.addWidget(self.count)
        self.photos = QListWidget()
        self.photos.setAccessibleName(tr('Received photos'))
        self.photos.setMinimumHeight(110)
        self.photos.itemDoubleClicked.connect(self._open_item)
        layout.addWidget(self.photos, 1)
        self.open_button = QPushButton(tr('Open selected photo'))
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(lambda: self._open_item(self.photos.currentItem()))
        self.photos.currentItemChanged.connect(lambda current, previous: self.open_button.setEnabled(current is not None))
        layout.addWidget(self.open_button, alignment=Qt.AlignmentFlag.AlignLeft)
        self.timer = QTimer(self)
        self.timer.setInterval(300)
        self.timer.timeout.connect(self.poll)
        self.refresh_networks()

    def refresh_networks(self):
        if self.receiver:
            return
        selected = self.network.currentData()
        self.network.clear()
        for name, address in local_addresses():
            self.network.addItem(f'{name} · {address}', address)
        if selected and self.network.findData(selected) >= 0:
            self.network.setCurrentIndex(self.network.findData(selected))
        self.start_button.setEnabled(self.network.count() > 0)
        if not self.network.count():
            self.status.setText(tr('Connect this computer to Wi-Fi or Ethernet, then refresh networks.'))

    def start(self):
        if self.receiver or not self.network.currentData():
            return
        try:
            receiver = PhoneReceiver(self.root, self.network.currentData(), mobile_texts())
            try:
                pixmap = qr_pixmap(receiver.url)
            except Exception:
                receiver.stop()
                raise
        except (OSError, ValueError, ImportError) as exc:
            self.status.setText(tr('Could not start the phone connection: {error}', error=str(exc)))
            return
        self.receiver = receiver
        self.qr.setPixmap(pixmap)
        self.qr.show()
        self.link.setText(receiver.url)
        self.link.show()
        self.copy_button.show()
        self.stop_button.show()
        self.start_button.hide()
        self.network.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.timer.start()
        self.poll()

    def poll(self):
        receiver = self.receiver
        if receiver is None:
            return
        while not receiver.events.empty():
            job_id, name = receiver.events.get_nowait()
            if job_id in self._received_ids:
                continue
            self._received_ids.add(job_id)
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, job_id)
            self.photos.insertItem(0, item)
            self.count.setText(tr('Received photos: {count}', count=len(self._received_ids)))
            self.photoImported.emit()
        if receiver.expired:
            self.stop()
            self.status.setText(tr('The phone connection expired. Connect again to get a new QR code.'))
            return
        self.status.setText(tr('Phone connected. Photos are saved automatically in Documents.')
                            if time.monotonic() - receiver.last_seen < 25 else
                            tr('Ready to receive. Scan the QR code with your phone camera.'))

    def stop(self):
        self.timer.stop()
        receiver, self.receiver = self.receiver, None
        if receiver:
            receiver.stop()
            # Do not lose UI notifications for photos committed just before stop.
            while not receiver.events.empty():
                job_id, name = receiver.events.get_nowait()
                if job_id not in self._received_ids:
                    self._received_ids.add(job_id)
                    item = QListWidgetItem(name)
                    item.setData(Qt.ItemDataRole.UserRole, job_id)
                    self.photos.insertItem(0, item)
                    self.photoImported.emit()
            self.count.setText(tr('Received photos: {count}', count=len(self._received_ids)))
        self.qr.clear()
        self.qr.hide()
        self.link.clear()
        self.link.hide()
        self.copy_button.hide()
        self.stop_button.hide()
        self.start_button.show()
        self.network.setEnabled(True)
        self.refresh_button.setEnabled(True)
        self.status.setText(tr('Phone connection is off.'))

    def _open_item(self, item):
        if item is not None:
            self.openRequested.emit(item.data(Qt.ItemDataRole.UserRole))
