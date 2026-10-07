"""Render the shared AST (see ast_nodes.py) into a LibreOffice Writer range
using the UNO API — real paragraph styles, character properties, lists,
hyperlinks and tables instead of literal Markdown/HTML characters.
"""

import urllib.parse

from .ast_nodes import (
    Blockquote,
    Bold,
    Code,
    CodeBlock,
    Heading,
    Italic,
    Link,
    ListItem,
    Paragraph,
    Table,
    Text,
)

# com.sun.star.style.ParagraphAdjust values (stable UNO constants).
_PARA_ADJUST = {"left": 0, "right": 1, "justify": 2, "center": 3}

_PARAGRAPH_BREAK = 0  # com.sun.star.text.ControlCharacter.PARAGRAPH_BREAK
_LINE_BREAK = 1  # com.sun.star.text.ControlCharacter.LINE_BREAK

# Links come from LLM-generated Markdown/HTML, which may be steered by a
# prompt injection hidden in the source document. Only these schemes become
# real clickable hyperlinks; anything else (vnd.sun.star.script:, file://,
# javascript:, a bare/relative reference, ...) is dropped so the text stays
# plain — this blocks macro-execution and local-file-open vectors on click.
_ALLOWED_LINK_SCHEMES = {"http", "https", "mailto"}


def _sanitize_link_url(url):
    """Return *url* if its scheme is allow-listed, else "" (plain text)."""
    if not url:
        return ""
    try:
        scheme = urllib.parse.urlsplit(url).scheme.lower()
    except ValueError:
        return ""
    return url if scheme in _ALLOWED_LINK_SCHEMES else ""


def write_blocks(model, text_obj, cursor, blocks, base_char_style=None, base_para_style=None):
    """Insert *blocks* at *cursor*, replacing markup with native Writer formatting."""
    if not blocks:
        return
    try:
        base_font = cursor.getPropertyValue("CharFontName")
    except Exception:
        base_font = ""
    for index, block in enumerate(blocks):
        if index > 0:
            text_obj.insertControlCharacter(cursor, _PARAGRAPH_BREAK, False)
        _write_block(model, text_obj, cursor, block, base_char_style, base_para_style, base_font)
    _set_para_style(cursor, base_para_style or "Standard")


def _write_block(model, text_obj, cursor, block, base_char_style, base_para_style, base_font):
    if isinstance(block, Heading):
        _set_para_style(cursor, f"Heading {min(max(block.level, 1), 6)}")
        _apply_align(cursor, block.align)
        _write_inlines(text_obj, cursor, block.inlines, base_char_style, base_font)
    elif isinstance(block, Paragraph):
        _set_para_style(cursor, base_para_style or "Standard")
        _apply_align(cursor, block.align)
        for line_index, line in enumerate(block.lines):
            if line_index > 0:
                text_obj.insertControlCharacter(cursor, _LINE_BREAK, False)
            _write_inlines(text_obj, cursor, line, base_char_style, base_font)
    elif isinstance(block, ListItem):
        style = _list_style(block.ordered, block.level)
        _set_para_style(cursor, style)
        _write_inlines(text_obj, cursor, block.inlines, base_char_style, base_font)
    elif isinstance(block, Blockquote):
        _set_para_style(cursor, "Quotations")
        _write_inlines(text_obj, cursor, block.inlines, base_char_style, base_font)
    elif isinstance(block, CodeBlock):
        _set_para_style(cursor, "Preformatted Text")
        text_obj.insertString(cursor, block.text, False)
    elif isinstance(block, Table):
        _write_table(model, text_obj, cursor, block)


def _list_style(ordered, level):
    base = "List Number" if ordered else "List Bullet"
    return base if level <= 1 else f"{base} {min(level, 5)}"


def _set_para_style(cursor, style_name):
    try:
        cursor.setPropertyValue("ParaStyleName", style_name)
    except Exception:
        pass


def _apply_align(cursor, align):
    if not align:
        return
    value = _PARA_ADJUST.get(align)
    if value is None:
        return
    try:
        cursor.setPropertyValue("ParaAdjust", value)
    except Exception:
        pass


def _write_inlines(text_obj, cursor, inlines, base_char_style, base_font,
                   bold=False, italic=False, link_url=None):
    for node in inlines:
        if isinstance(node, Text):
            _write_run(text_obj, cursor, node.value, base_char_style, base_font,
                       bold, italic, False, link_url)
        elif isinstance(node, Bold):
            _write_inlines(text_obj, cursor, node.children, base_char_style, base_font,
                           True, italic, link_url)
        elif isinstance(node, Italic):
            _write_inlines(text_obj, cursor, node.children, base_char_style, base_font,
                           bold, True, link_url)
        elif isinstance(node, Code):
            _write_run(text_obj, cursor, node.value, base_char_style, base_font,
                       bold, italic, True, link_url)
        elif isinstance(node, Link):
            _write_inlines(text_obj, cursor, node.children, base_char_style, base_font,
                           bold, italic, node.url)


def _write_run(text_obj, cursor, value, base_char_style, base_font, bold, italic, code, link_url):
    if not value:
        return
    try:
        cursor.setPropertyValue("CharStyleName", base_char_style or "Default Style")
    except Exception:
        pass
    try:
        cursor.setPropertyValue("CharWeight", 150.0 if bold else 100.0)  # BOLD / NORMAL
    except Exception:
        pass
    try:
        cursor.setPropertyValue("CharPosture", 2 if italic else 0)  # ITALIC / NONE
    except Exception:
        pass
    try:
        cursor.setPropertyValue("CharFontName", "Consolas" if code else base_font)
    except Exception:
        pass
    try:
        cursor.setPropertyValue("HyperLinkURL", _sanitize_link_url(link_url))
    except Exception:
        pass
    text_obj.insertString(cursor, value, False)


def _write_table(model, text_obj, cursor, table):
    rows = [table.header] + list(table.rows) if table.header else list(table.rows)
    if not rows:
        return
    col_count = max(len(row) for row in rows)
    try:
        uno_table = model.createInstance("com.sun.star.text.TextTable")
        uno_table.initialize(len(rows), col_count)
        text_obj.insertTextContent(cursor, uno_table, False)
    except Exception:
        return

    for row_index, row in enumerate(rows):
        for col_index in range(col_count):
            cell_name = f"{chr(ord('A') + col_index)}{row_index + 1}"
            try:
                cell = uno_table.getCellByName(cell_name)
            except Exception:
                continue
            inlines = row[col_index] if col_index < len(row) else []
            cell.setString(_plain_text(inlines))
            if table.header and row_index == 0:
                try:
                    cell_cursor = cell.createTextCursor()
                    cell_cursor.gotoStart(False)
                    cell_cursor.gotoEnd(True)
                    cell_cursor.setPropertyValue("CharWeight", 150.0)
                except Exception:
                    pass


def _plain_text(inlines):
    parts = []
    for node in inlines:
        if isinstance(node, (Text, Code)):
            parts.append(node.value)
        elif isinstance(node, (Bold, Italic, Link)):
            parts.append(_plain_text(node.children))
    return "".join(parts)
