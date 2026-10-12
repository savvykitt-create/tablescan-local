"""Review decisions and navigation, independent of Qt and persistence."""
from .table_checks import flag_table_outliers


def confirm_value(page, item, value, template):
    if item.status == 'excluded':
        return False
    item.final_text = value.strip()
    item.status = 'confirmed' if item.final_text == item.raw_text else 'corrected'
    flag_table_outliers(page, template)
    return True


def confirmable_items(result):
    items, blocked = [], 0
    for page in result.pages:
        for item in page.fields:
            if not item.needs_review:
                continue
            region = next((r for r in result.template.fields if r.id == item.region_id), None)
            if region is None or region.hard_errors(item.final_text):
                blocked += 1
            else:
                items.append(item)
        for item in page.cells:
            if not item.needs_review:
                continue
            constraints, _ = result.template.cell_constraints(item.row, item.column)
            if constraints and constraints.hard_errors(item.final_text):
                blocked += 1
            else:
                items.append(item)
    return items, blocked


def set_axis_excluded(page, template, axis, index, checked):
    page.set_excluded(axis, index, checked, template)
    flag_table_outliers(page, template)


def next_uncertain(result, page_index, kind, index, *, page_only=False):
    """Visit review order cyclically without copying every cell in the document."""
    if not result.pages:
        return None
    page_index = max(0, min(page_index, len(result.pages) - 1))
    page = result.pages[page_index]
    selected = page.fields if kind == 'field' else page.cells
    offset = index + (len(page.fields) if kind == 'cell' else 0) if 0 <= index < len(selected) else -1

    def scan(p, start=0, stop=None):
        page = result.pages[p]
        end = len(page.fields) + len(page.cells) if stop is None else stop
        for position in range(start, end):
            field = position < len(page.fields)
            i = position if field else position - len(page.fields)
            item = (page.fields if field else page.cells)[i]
            if item.needs_review:
                return p, 'field' if field else 'cell', i, item
        return None

    target = scan(page_index, offset + 1)
    if target is not None:
        return target
    if not page_only:
        for step in range(1, len(result.pages)):
            target = scan((page_index + step) % len(result.pages))
            if target is not None:
                return target
    return scan(page_index, 0, offset + 1)
