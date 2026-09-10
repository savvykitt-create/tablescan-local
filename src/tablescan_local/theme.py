from __future__ import annotations

import re
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QWidget

PALETTES = {
    "light": dict(bg="#F7F8F7", surface="#FFFFFF", soft="#EDF1F1", text="#243438", muted="#627477",
                  border="#CFDADA", scroll="#98AFB2", accent="#386E76", hover="#2D5D65", selection="#D7E9EC",
                  warning="#805C22", warning_bg="#FAEFD6", success="#376B55", success_bg="#E4F1E8",
                  excluded="#E8ECEC", danger="#A54242", danger_bg="#FAE5E3", canvas="#E5EBEB"),
    "dark": dict(bg="#1C262A", surface="#243136", soft="#2A393E", text="#E2EBEB", muted="#A7B9BC",
                 border="#455A60", scroll="#68858D", accent="#93C7CB", hover="#A9D7DA", selection="#36565F",
                 warning="#EDD095", warning_bg="#4C4230", success="#AFD5BD", success_bg="#304B40",
                 excluded="#354348", danger="#EEB1AB", danger_bg="#513738", canvas="#172024"),
}

# Legacy inline styles in the template editor share the same theme tokens.
LEGACY = {
    "#FFFFFF": "surface", "#20242B": "text", "#68707C": "muted", "#D7DCE3": "border",
    "#E2E5EA": "border", "#AEB7C4": "border", "#F7F8FA": "soft", "#F8FAFC": "soft",
    "#EFF6FF": "selection", "#EAF1FF": "selection", "#DBEAFE": "selection",
    "#4F46E5": "accent", "#6B8FEA": "accent", "#D97706": "warning", "#15803D": "success",
    "#FEF3C7": "warning_bg", "#92400E": "warning", "#ECFDF3": "success_bg",
    "#ECEFF3": "excluded", "#EEF1F5": "canvas", "#FEF2F2": "danger_bg", "#B91C1C": "danger",
}


def colors() -> dict[str, str]:
    app = QApplication.instance()
    return PALETTES.get(app.property("tablescanTheme") if app else "light", PALETTES["light"])


def themed_css(css: str) -> str:
    palette = colors()
    return re.sub(r"#[0-9a-fA-F]{6}", lambda m: palette.get(LEGACY.get(m[0].upper(), ""), m[0]), css)


def set_theme_style(widget: QWidget, css: str) -> None:
    widget.setProperty("tablescanInlineStyle", css)
    widget.setStyleSheet(themed_css(css))


def apply_theme(app: QApplication, mode: str) -> None:
    app.setProperty("tablescanTheme", mode if mode in PALETTES else "light")
    c = colors()
    palette = QPalette()
    for role, key in ((QPalette.ColorRole.Window, "bg"), (QPalette.ColorRole.Base, "surface"),
                      (QPalette.ColorRole.AlternateBase, "soft"), (QPalette.ColorRole.WindowText, "text"),
                      (QPalette.ColorRole.Text, "text"), (QPalette.ColorRole.Button, "surface"),
                      (QPalette.ColorRole.ButtonText, "text"), (QPalette.ColorRole.Highlight, "selection"),
                      (QPalette.ColorRole.HighlightedText, "text"), (QPalette.ColorRole.ToolTipBase, "surface"),
                      (QPalette.ColorRole.ToolTipText, "text"), (QPalette.ColorRole.PlaceholderText, "muted")):
        palette.setColor(role, QColor(c[key]))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(c["muted"]))
    app.setPalette(palette)
    app.setStyleSheet(f"""
        QWidget {{ color: {c['text']}; font-size: 13px; }}
        QMainWindow, QDialog {{ background: {c['bg']}; }}
        QLabel {{ background: transparent; }}
        QPushButton, QToolButton {{ min-height: 32px; padding: 0 12px; border: 1px solid {c['border']};
            border-radius: 6px; background: {c['surface']}; font-weight: 600; }}
        QPushButton:hover, QToolButton:hover {{ background: {c['selection']}; border-color: {c['accent']}; }}
        QPushButton:pressed, QToolButton:pressed {{ background: {c['soft']}; }}
        QPushButton[primary="true"] {{ background: {c['accent']}; color: {c['bg']}; border-color: {c['accent']}; }}
        QPushButton[primary="true"]:hover {{ background: {c['hover']}; }}
        QPushButton:disabled, QToolButton:disabled {{ color: {c['muted']}; background: {c['soft']}; border-color: {c['border']}; }}
        QPushButton:focus, QToolButton:focus {{ border: 2px solid {c['accent']}; }}
        QPushButton[danger="true"] {{ color: {c['danger']}; }}
        QLineEdit, QComboBox, QSpinBox {{ min-height: 32px; padding: 0 9px; border: 1px solid {c['border']};
            border-radius: 6px; background: {c['surface']}; selection-background-color: {c['selection']}; selection-color: {c['text']}; }}
        QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border: 2px solid {c['accent']}; }}
        QLineEdit:disabled {{ color: {c['muted']}; background: {c['soft']}; }}
        QComboBox QAbstractItemView {{ background: {c['surface']}; selection-background-color: {c['selection']}; }}
        QTabWidget::pane {{ border: 1px solid {c['border']}; background: {c['surface']}; }}
        QTabBar::tab {{ padding: 10px 18px; color: {c['muted']}; background: {c['soft']}; }}
        QTabBar::tab:selected {{ color: {c['text']}; background: {c['surface']}; border-bottom: 2px solid {c['accent']}; font-weight: 600; }}
        QTableWidget {{ background: {c['surface']}; alternate-background-color: {c['soft']}; border: 1px solid {c['border']};
            gridline-color: {c['border']}; selection-background-color: {c['selection']}; selection-color: {c['text']}; outline: none; }}
        QTableWidget::item:selected {{ background: {c['selection']}; color: {c['text']}; }}
        QHeaderView::section {{ background: {c['soft']}; color: {c['text']}; border: 0; border-right: 1px solid {c['border']};
            border-bottom: 1px solid {c['border']}; padding: 7px; font-weight: 600; }}
        QTableCornerButton::section {{ background: {c['soft']}; border: 1px solid {c['border']}; }}
        QListWidget {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 6px; outline: none; }}
        QListWidget::item {{ min-height: 34px; padding: 3px 8px; }}
        QListWidget::item:selected {{ background: {c['selection']}; color: {c['text']}; }}
        QGroupBox {{ border: 0; margin-top: 12px; font-weight: 600; }}
        QGroupBox::title {{ subcontrol-origin: margin; padding-bottom: 8px; }}
        QCheckBox {{ spacing: 8px; min-height: 26px; }}
        QSplitter::handle {{ background: {c['border']}; }}
        QSplitter::handle:hover {{ background: {c['accent']}; }}
        QScrollBar:vertical {{ background: {c['soft']}; width: 12px; margin: 0; }}
        QScrollBar:horizontal {{ background: {c['soft']}; height: 12px; margin: 0; }}
        QScrollBar::handle {{ background: {c['scroll']}; border-radius: 5px; }}
        QScrollBar::handle:vertical {{ min-height: 32px; margin: 2px; }}
        QScrollBar::handle:horizontal {{ min-width: 32px; margin: 2px; }}
        QScrollBar::handle:hover {{ background: {c['accent']}; }}
        QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
        QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
        QFrame#reviewInspector {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 8px; }}
        QLabel#cropPreview {{ background: {c['canvas']}; border: 1px solid {c['border']}; border-radius: 6px; }}
        QScrollArea {{ background: {c['surface']}; }}
        QToolTip {{ background: {c['surface']}; color: {c['text']}; border: 1px solid {c['border']}; padding: 6px; }}
    """)
    for widget in app.allWidgets():
        css = widget.property("tablescanInlineStyle")
        if css:
            widget.setStyleSheet(themed_css(css))
        refresh = getattr(widget, "refresh_theme", None)
        if callable(refresh):
            refresh()
