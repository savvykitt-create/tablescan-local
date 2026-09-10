"""Qt widgets that retain explicitly translated text for in-place language switching."""
from __future__ import annotations

from PySide6 import QtGui, QtWidgets
from .i18n import Text, bind, render, unbind


_SIMPLE = ("setText", "setWindowTitle", "setToolTip", "setStatusTip", "setWhatsThis", "setTitle",
           "setPlaceholderText", "setAccessibleName", "setAccessibleDescription", "setSpecialValueText",
           "setLabelText", "setCancelButtonText")


def _widget(base):
    class Localized(base):
        def __init__(self, *args, **kwargs):
            super().__init__(*(render(arg) for arg in args), **{k: render(v) for k, v in kwargs.items()})
            # Text constructors: label/button/action/item/group box, with optional icon.
            for arg in args:
                if isinstance(arg, Text):
                    method = "setTitle" if issubclass(base, QtWidgets.QGroupBox) else "setText"
                    if hasattr(self, method):
                        getattr(self, method)(arg)
                    break
            for name, value in kwargs.items():
                if isinstance(value, Text):
                    setter = "set" + name[0].upper() + name[1:]
                    if hasattr(self, setter):
                        getattr(self, setter)(value)
            if isinstance(self, QtWidgets.QLineEdit):
                self.textEdited.connect(lambda _text: unbind(self, "setText"))

    for name in _SIMPLE:
        if hasattr(base, name):
            def setter(self, *args, _method=name):
                bind(self, _method, _method, args)
                return getattr(base, _method)(self, *(render(arg) for arg in args))
            setattr(Localized, name, setter)
    Localized.__name__ = base.__name__
    Localized.__qualname__ = base.__name__
    return Localized


for _name in ("QWidget", "QFrame", "QLabel", "QPushButton", "QToolButton", "QRadioButton", "QCheckBox",
              "QLineEdit", "QSpinBox", "QGroupBox", "QDialog", "QMainWindow", "QTableWidgetItem", "QListWidgetItem"):
    globals()[_name] = _widget(getattr(QtWidgets, _name))
QAction = _widget(QtGui.QAction)


class QComboBox(_widget(QtWidgets.QComboBox)):
    def addItem(self, *args):
        index = self.count()
        super().addItem(*(render(arg) for arg in args))
        text = next((arg for arg in args if isinstance(arg, str)), "")
        self.setItemText(index, text)

    def addItems(self, texts):
        for text in texts:
            self.addItem(text)

    def setItemText(self, index, text):
        bind(self, ("item", index), "setItemText", (index, text))
        super().setItemText(index, render(text))

    def clear(self):
        unbind(self)
        super().clear()


class QTabWidget(_widget(QtWidgets.QTabWidget)):
    def addTab(self, widget, *args):
        index = super().addTab(widget, *(render(arg) for arg in args))
        self.setTabText(index, args[-1])
        return index

    def setTabText(self, index, text):
        bind(self, ("tab", index), "setTabText", (index, text))
        super().setTabText(index, render(text))


class QFormLayout(QtWidgets.QFormLayout):
    def addRow(self, *args):
        if args and isinstance(args[0], Text):
            label = QLabel(args[0])
            if len(args) > 1 and isinstance(args[1], QtWidgets.QWidget):
                label.setBuddy(args[1])
            args = (label, *args[1:])
        super().addRow(*args)


class QTableWidget(_widget(QtWidgets.QTableWidget)):
    def setHorizontalHeaderLabels(self, labels):
        for column, text in enumerate(labels):
            self.setHorizontalHeaderItem(column, QTableWidgetItem(text))


class QListWidget(_widget(QtWidgets.QListWidget)):
    def addItem(self, item):
        super().addItem(QListWidgetItem(item) if isinstance(item, Text) else item)

    def addItems(self, items):
        for item in items:
            self.addItem(item)


class QDialogButtonBox(_widget(QtWidgets.QDialogButtonBox)):
    # Standard Qt buttons are created in C++; mark overrides through a small proxy.
    def button(self, standard):
        button = super().button(standard)
        if button is not None and not getattr(button, "_localized_setter", False):
            native_setter = button.setText
            def set_text(text):
                bind(button, "setText", "setText", (text,))
                native_setter(render(text))
            button.setText = set_text
            button._localized_setter = True
        return button


class QProgressDialog(_widget(QtWidgets.QProgressDialog)):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if args and isinstance(args[0], Text):
            self.setLabelText(args[0])
        if len(args) > 1 and isinstance(args[1], Text):
            self.setCancelButtonText(args[1])


class QFileDialog(QtWidgets.QFileDialog):
    """Qt dialogs obey the app language even when the OS uses another language."""
    @staticmethod
    def getOpenFileName(*args, **kwargs):
        kwargs["options"] = kwargs.get("options", QtWidgets.QFileDialog.Option(0)) | QtWidgets.QFileDialog.Option.DontUseNativeDialog
        return QtWidgets.QFileDialog.getOpenFileName(*(render(a) for a in args), **kwargs)

    @staticmethod
    def getOpenFileNames(*args, **kwargs):
        kwargs["options"] = kwargs.get("options", QtWidgets.QFileDialog.Option(0)) | QtWidgets.QFileDialog.Option.DontUseNativeDialog
        return QtWidgets.QFileDialog.getOpenFileNames(*(render(a) for a in args), **kwargs)

    @staticmethod
    def getSaveFileName(*args, **kwargs):
        kwargs["options"] = kwargs.get("options", QtWidgets.QFileDialog.Option(0)) | QtWidgets.QFileDialog.Option.DontUseNativeDialog
        return QtWidgets.QFileDialog.getSaveFileName(*(render(a) for a in args), **kwargs)
