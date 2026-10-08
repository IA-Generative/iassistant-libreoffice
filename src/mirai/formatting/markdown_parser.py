"""Block-level Markdown parser producing the shared AST (see ast_nodes.py).

Targets exactly the constructs LLMs commonly emit — headings, emphasis,
lists, blockquotes, links, simple pipe tables, fenced code — not full
CommonMark. Plain prose with no markers simply becomes a single Paragraph.
"""

import re

from .ast_nodes import Blockquote, CodeBlock, Heading, ListItem, Paragraph, Table
from .inline_parser import parse_inline

_RE_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_RE_UL_ITEM = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_RE_OL_ITEM = re.compile(r"^(\s*)\d+[.)]\s+(.*)$")
_RE_QUOTE = re.compile(r"^>\s?(.*)$")
_RE_HR = re.compile(r"^(-{3,}|\*{3,}|_{3,})\s*$")
_RE_FENCE = re.compile(r"^```")
_RE_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def parse_markdown(text):
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]

        if not line.strip():
            i += 1
            continue

        if _RE_FENCE.match(line):
            i += 1
            code_lines = []
            while i < n and not _RE_FENCE.match(lines[i]):
                code_lines.append(lines[i])
                i += 1
            i += 1  # skip closing fence (tolerate a missing one at EOF)
            blocks.append(CodeBlock("\n".join(code_lines)))
            continue

        m = _RE_HEADING.match(line)
        if m:
            blocks.append(Heading(len(m.group(1)), parse_inline(m.group(2).strip()), None))
            i += 1
            continue

        if _RE_HR.match(line):
            i += 1
            continue

        m = _RE_QUOTE.match(line)
        if m:
            quote_lines = [m.group(1)]
            i += 1
            while i < n and _RE_QUOTE.match(lines[i]):
                quote_lines.append(_RE_QUOTE.match(lines[i]).group(1))
                i += 1
            blocks.append(Blockquote(parse_inline(" ".join(quote_lines).strip())))
            continue

        m = _RE_UL_ITEM.match(line)
        if m:
            level = 2 if len(m.group(1)) >= 2 else 1
            blocks.append(ListItem(False, level, parse_inline(m.group(2).strip())))
            i += 1
            continue

        m = _RE_OL_ITEM.match(line)
        if m:
            level = 2 if len(m.group(1)) >= 2 else 1
            blocks.append(ListItem(True, level, parse_inline(m.group(2).strip())))
            i += 1
            continue

        if "|" in line and i + 1 < n and _RE_TABLE_SEP.match(lines[i + 1]):
            header_cells = _split_table_row(line)
            i += 2  # header + separator
            rows = []
            while i < n and "|" in lines[i] and lines[i].strip():
                rows.append(_split_table_row(lines[i]))
                i += 1
            blocks.append(Table(
                [parse_inline(c) for c in header_cells],
                [[parse_inline(c) for c in row] for row in rows],
            ))
            continue

        # Paragraph: consume consecutive plain lines. Each line becomes a
        # soft line-break inside the same Writer paragraph; a blank line
        # (handled above) is what actually starts a new paragraph.
        para_lines = [line]
        i += 1
        while i < n and lines[i].strip() and not _starts_new_block(lines[i], lines[i + 1] if i + 1 < n else ""):
            para_lines.append(lines[i])
            i += 1
        blocks.append(Paragraph([parse_inline(l.strip()) for l in para_lines], None))

    return blocks


def _starts_new_block(line, next_line):
    return bool(
        _RE_HEADING.match(line) or _RE_UL_ITEM.match(line) or _RE_OL_ITEM.match(line)
        or _RE_QUOTE.match(line) or _RE_HR.match(line) or _RE_FENCE.match(line)
        or ("|" in line and _RE_TABLE_SEP.match(next_line or ""))
    )


def _split_table_row(line):
    row = line.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|"):
        row = row[:-1]
    return [cell.strip() for cell in row.split("|")]
