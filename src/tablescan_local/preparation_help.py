"""Contextual action help for the template and document preparation workspace."""
from PySide6.QtWidgets import QPushButton, QToolButton, QToolBar, QWidget
from .button_help import button_with_help, HelpButton
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
        tr('Export printable Excel…'): tr('Create an editable Excel form from the current template, with page size and print layout settings.'),
        tr('+ Добавить область'): tr('Draw an area outside the table for a date, name or other document field.'),
        tr('Предложить автоматически'): tr('Suggest common document fields. Inspect their positions and names before saving.'),
        tr('Перерисовать область'): tr('Draw a new rectangle for the selected field on the image.'),
        tr('Удалить поле'): tr('Remove the selected field from this template.'),
        tr('Сохранить поле'): tr('Apply the name, recognition type and export settings to the selected field.'),
        tr('Формат и допустимые значения…'): tr('Set the value type, range and allowed values for this column.'),
        tr('Сохранить правила столбца'): tr('Apply these defaults to the selected column. Cell-region rules take priority.'),
        tr('Все измерения'): tr('Select all measurement cells, excluding headers and row labels.'),
        tr('Снять'): tr('Clear the cell selection without changing any rules.'),
        tr('Применить к выделению'): tr('Apply the displayed value rule to the selected cells.'),
        tr('Дополнительно…'): tr('Open the full value-rule editor for the selected cells.'),
        tr('Изменить…'): tr('Edit the selected rule region.'),
        tr('Удалить правило'): tr('Remove the selected region rule. Column defaults will apply again.'),
        tr('Создать из образца'): tr('Choose a sample image or PDF and build a reusable template from its table.'),
        tr('Импорт JSON'): tr('Import a TableScan template file containing its grid and recognition rules.'),
        tr('Дублировать'): tr('Create an independent copy of the selected template.'),
        tr('Удалить'): tr('Delete the selected template from the library after confirmation.'),
        tr('Открыть редактор'): tr('Open the selected template to edit its grid, fields, rules and printable form.'),
        tr('Save Excel form…'): tr('Export the selected library template as an editable printable Excel workbook.'),
    }
    for button in page.findChildren(QWidget):
        if not isinstance(button, (QPushButton, QToolButton)):
            continue
        if button.property('hasActionHelp') or button.text() == '?':
            continue
        description = descriptions.get(button.text()) or button.toolTip()
        if not description or isinstance(button.parentWidget(), QToolBar):
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

    descriptions = [
        tr('Zoom out to see more of the document.'),
        tr('Zoom in to inspect table guides and cells.'),
        tr('Fit the entire document in the preview.'),
        tr('Rotate the document counterclockwise by 90 degrees.'),
        tr('Rotate the document clockwise by 90 degrees.'),
        tr('Choose cells, rows or columns to skip during recognition.'),
    ]
    index = 0
    for toolbar in page.findChildren(QToolBar):
        for action in list(toolbar.actions()):
            if index >= len(descriptions):
                break
            description = descriptions[index]
            action.setToolTip(description)
            next_actions = toolbar.actions()
            position = next_actions.index(action) + 1
            before = next_actions[position] if position < len(next_actions) else None
            help_button = HelpButton(description, toolbar)
            toolbar.insertWidget(before, help_button) if before else toolbar.addWidget(help_button)
            index += 1
