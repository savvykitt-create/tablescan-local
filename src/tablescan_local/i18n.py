"""Explicit UI translations. Document contents are never looked up in the catalog."""
from __future__ import annotations

import html
import json
import weakref
from pathlib import Path
from typing import Callable

SUPPORTED_LANGUAGES = {"en": "English", "cs": "Čeština", "ru": "Русский"}
_language = "en"
_catalog = json.loads((Path(__file__).parent / "translations" / "messages.json").read_text(encoding="utf-8"))
_bindings = {}
_qt_translator = None


class Text(str):
    """A string with a retained translation recipe, for updating an existing widget."""
    def __new__(cls, recipe: Callable[[], str]):
        value = super().__new__(cls, recipe())
        value.recipe = recipe
        return value

    def render(self) -> str:
        return self.recipe()

    def __str__(self) -> str:
        return self.render()

    def __add__(self, other):
        return join_text("", (self, other))

    def __radd__(self, other):
        return join_text("", (other, self))

    def __deepcopy__(self, memo):
        # Persisted names/audit evidence are a snapshot, not a live UI binding.
        return self.render()


def render(value):
    return value.render() if isinstance(value, Text) else value


def fmt(template: str, **values) -> Text:
    return Text(lambda: template.format(**{key: render(value) for key, value in values.items()}))


def tr(source: str, **values) -> Text:
    return Text(lambda: _catalog.get(source, {}).get(_language, source).format(
        **{key: render(value) for key, value in values.items()}))


def join_text(separator: str, values) -> Text:
    parts = tuple(values)
    return Text(lambda: str(render(separator)).join(str(render(part)) for part in parts))


def escape_text(value: str, quote: bool = True) -> Text:
    return Text(lambda: html.escape(str(render(value)), quote=quote))


def language() -> str:
    return _language


def bind(obj, key, method: str, args) -> None:
    identity = id(obj)
    if identity not in _bindings:
        _bindings[identity] = (weakref.ref(obj, lambda _ref, key=identity: _bindings.pop(key, None)), {})
    records = _bindings[identity][1]
    if any(isinstance(value, Text) for value in args):
        records[key] = (method, tuple(args))
    else:
        records.pop(key, None)


def unbind(obj, key=None) -> None:
    if key is None:
        _bindings.pop(id(obj), None)
    else:
        _bindings.get(id(obj), (None, {}))[1].pop(key, None)


def set_language(code: str) -> None:
    global _language, _qt_translator
    from PySide6.QtCore import QLocale, QTranslator
    from PySide6.QtWidgets import QApplication
    _language = code if code in SUPPORTED_LANGUAGES else "en"
    app = QApplication.instance()
    if app is None:
        return
    QLocale.setDefault(QLocale({"en": "en_GB", "cs": "cs_CZ", "ru": "ru_RU"}[_language]))
    if _qt_translator is not None:
        app.removeTranslator(_qt_translator)
        _qt_translator = None
    if _language != "en":
        translator = QTranslator(app)
        if translator.load(str(Path(__file__).parent / "translations" / f"qtbase_{_language}.qm")):
            app.installTranslator(translator)
            _qt_translator = translator
    app.setProperty("tablescanLanguage", _language)
    # Only explicitly marked UI strings are bound. Plain user text is untouched.
    for reference, records in list(_bindings.values()):
        obj = reference()
        if obj is None:
            continue
        for key, (method, args) in list(records.items()):
            try:
                from PySide6.QtCore import QObject, QSignalBlocker
                blocker = QSignalBlocker(obj) if isinstance(obj, QObject) else None
                getattr(obj, method)(*(render(arg) for arg in args))
                records[key] = (method, args)
                del blocker
            except RuntimeError:
                # Qt can own and delete a child while its Python wrapper still exists.
                unbind(obj)
                break


def localized_rule_name(value: str):
    """Translate only known factory labels, including legacy saved labels."""
    key = "__builtin_rule__" + value
    return Text(lambda: _catalog[key][_language]) if key in _catalog else value


def localized_saved_message(value: str):
    """Re-render known fixed messages saved by an earlier language session."""
    for source, versions in _catalog.items():
        if value == source or value in versions.values():
            return Text(lambda versions=versions: versions[_language])
    # Progress messages are persisted as rendered text. Recover only known
    # catalog patterns; arbitrary filenames and document values stay untouched.
    import re
    from string import Formatter
    for source, versions in _catalog.items():
        if "{" not in source or source.startswith("__builtin_rule__"):
            continue
        for pattern in {source, *versions.values()}:
            parts = list(Formatter().parse(pattern))
            if any(spec or conversion for _, _, spec, conversion in parts):
                continue
            if sum(len(literal) for literal, _, _, _ in parts) < 12:
                continue
            names, expression = [], ""
            for literal, field, _, _ in parts:
                expression += re.escape(literal)
                if field is not None:
                    if field in names:
                        expression += "\\" + str(names.index(field) + 1)
                    else:
                        names.append(field)
                        expression += "(.*?)"
            match = re.fullmatch(expression, value, re.DOTALL)
            if match:
                values = dict(zip(names, match.groups()))
                return Text(lambda versions=versions, values=values: versions[_language].format(**values))
    return value
