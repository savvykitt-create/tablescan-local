"""Contextual action help for the template and document preparation workspace."""
from PySide6.QtWidgets import QPushButton, QToolButton, QToolBar, QWidget
from .button_help import button_with_help, HelpButton, suppress_action_tooltip
from .i18n import tr


def add_preparation_help(page):
    descriptions = {
        tr('Проверить шаблон'): tr('Check the grid, fields and value rules for errors before saving.'),
        tr('Сохранить шаблон'): tr('Save the current grid, fields and rules in the template library for reuse.'),
        tr('Use for this file'): tr('Apply these template changes only to the current document.'),
        tr('Подогнать шаблон к таблице'): tr('Fit the template guides to the detected table while keeping its rules.'),
        tr('Align photograph — four corners'): tr('Mark the four table corners to straighten a photograph before adjusting the grid.'),
        tr('Найти сетку заново'): tr('Detect table borders and guides again. Check the new grid before analysis.'),
        tr('Применить выбранный шаблон'): tr('Load the selected saved template, including its grid, fields and rules.'),
        tr('Перерисовать границу таблицы'): tr('Drag a rectangle around the table on the image to replace its boundary.'),
        tr('Export printable Excel…'): tr('Create an editable Excel form that preserves the template grid and field positions for printing and recognition.'),
        tr('+ Добавить область'): tr('Draw an area outside the table for a date, name or other document field.'),
        tr('Предложить автоматически'): tr('Suggest common document fields. Inspect their positions and names before saving.'),
        tr('Перерисовать область'): tr('Draw a new rectangle for the selected field on the image.'),
        tr('Сохранить поле'): tr('Apply the name, recognition type and export settings to the selected field.'),
        tr('Формат и допустимые значения…'): tr('Set the value type, range and allowed values for this column.'),
        tr('Сохранить правила столбца'): tr('Apply these defaults to the selected column. Cell-region rules take priority.'),
        tr('Все измерения'): tr('Select all measurement cells, excluding headers and row labels.'),
        tr('Применить к выделению'): tr('Apply the displayed value rule to the selected cells.'),
        tr('Дополнительно…'): tr('Open the full value-rule editor for the selected cells.'),
        tr('Создать из образца'): tr('Choose a sample image or PDF and build a reusable template from its table.'),
        tr('Импорт JSON'): tr('Import a TableScan template file containing its grid and recognition rules.'),
        tr('Save Excel form…'): tr('Export the selected library template as an editable printable Excel workbook.'),
    }
    for button in page.findChildren(QWidget):
        if not isinstance(button, (QPushButton, QToolButton)):
            continue
        if button.property('hasActionHelp') or button.text() == '?':
            continue
        description = descriptions.get(button.text()) or getattr(button, '_action_help_text', None)
        if not description:
            if button.toolTip():
                button.setAccessibleDescription(button.toolTip())
            suppress_action_tooltip(button)
            continue
        if isinstance(button.parentWidget(), QToolBar):
            continue
        parent = button.parentWidget()
        layout = parent.layout() if parent else None
        if layout is None:
            continue
        hidden = button.isHidden()
        placeholder = QWidget(parent)
        item = layout.replaceWidget(button, placeholder)
        if item is None:
            placeholder.deleteLater()
            continue
        wrapper = button_with_help(button, description)
        layout.replaceWidget(placeholder, wrapper)
        wrapper.setVisible(not hidden)
        placeholder.deleteLater()

    # Zoom, fit and rotation are standard, self-explanatory controls. Do not
    # split their compact toolbar with question marks. Keep one contextual
    # explanation for the less obvious skip-cells action.
    skip_button = getattr(page, 'exclusions_button', None)
    for toolbar in page.findChildren(QToolBar):
        for action in list(toolbar.actions()):
            widget = toolbar.widgetForAction(action)
            if isinstance(widget, HelpButton):
                continue
            action.setToolTip('')
            if widget is not None:
                suppress_action_tooltip(widget)
            if widget is skip_button and not widget.property('hasActionHelp'):
                description = tr('Choose cells, rows or columns to skip during recognition.')
                widget.setAccessibleDescription(description)
                help_button = HelpButton(description, toolbar)
                index = toolbar.actions().index(action) + 1
                before = toolbar.actions()[index] if index < len(toolbar.actions()) else None
                toolbar.insertWidget(before, help_button) if before else toolbar.addWidget(help_button)
                widget.setProperty('hasActionHelp', True)
