"""HTML -> shared AST conversion, built on the stdlib html.parser.HTMLParser.

Covers the tags LLMs actually produce when they answer in HTML instead of
Markdown: headings, paragraphs, bold/italic/code, links, lists, blockquotes
and tables. A `style="text-align:..."` attribute on a heading/paragraph maps
to Writer paragraph alignment.
"""

import re
from html.parser import HTMLParser

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

_HEADING_LEVELS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
_ALIGN_RE = re.compile(r"text-align\s*:\s*(left|right|center|justify)", re.IGNORECASE)


def _align_from_attrs(attrs):
    for key, value in attrs:
        if key == "style" and value:
            m = _ALIGN_RE.search(value)
            if m:
                return m.group(1).lower()
    return None


class _BlockCollector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks = []
        self._inline_stack = [[]]
        self._format_stack = []  # "bold" | "italic" | "code"
        self._list_stack = []    # ordered flag per open <ul>/<ol>
        self._align = None
        self._pre_depth = 0
        self._code_buffer = None
        self._table = None
        self._row = None

    def _flush_top_paragraph(self):
        children = self._inline_stack[-1]
        if children:
            self.blocks.append(Paragraph([children], self._align))
        self._inline_stack[-1] = []
        self._align = None

    def _wrap_with_open_formats(self, node):
        for fmt in reversed(self._format_stack):
            if fmt == "bold":
                node = Bold([node])
            elif fmt == "italic":
                node = Italic([node])
        return node

    def handle_starttag(self, tag, attrs):
        if tag in _HEADING_LEVELS:
            self._flush_top_paragraph()
            self._inline_stack.append([])
            self._align = _align_from_attrs(attrs)
        elif tag in ("p", "div"):
            self._flush_top_paragraph()
            self._align = _align_from_attrs(attrs)
        elif tag in ("strong", "b"):
            self._format_stack.append("bold")
        elif tag in ("em", "i"):
            self._format_stack.append("italic")
        elif tag == "code" and self._pre_depth == 0:
            self._format_stack.append("code")
        elif tag == "pre":
            self._pre_depth += 1
            self._code_buffer = []
        elif tag == "a":
            self._inline_stack.append([])
            self._format_stack.append("link:" + dict(attrs).get("href", ""))
        elif tag in ("ul", "ol"):
            self._list_stack.append(tag == "ol")
        elif tag == "li":
            self._inline_stack.append([])
        elif tag == "blockquote":
            self._inline_stack.append([])
        elif tag == "br":
            self._inline_stack[-1].append(Text("\n"))
        elif tag == "table":
            self._table = {"header": None, "rows": []}
        elif tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._inline_stack.append([])

    def handle_endtag(self, tag):
        if tag in _HEADING_LEVELS:
            children = self._inline_stack.pop()
            self.blocks.append(Heading(_HEADING_LEVELS[tag], children, self._align))
            self._align = None
        elif tag in ("p", "div"):
            self._flush_top_paragraph()
        elif tag in ("strong", "b", "em", "i", "code"):
            if self._format_stack:
                self._format_stack.pop()
        elif tag == "pre":
            self._pre_depth -= 1
            self.blocks.append(CodeBlock("".join(self._code_buffer or [])))
            self._code_buffer = None
        elif tag == "a":
            children = self._inline_stack.pop()
            url = ""
            if self._format_stack and self._format_stack[-1].startswith("link:"):
                url = self._format_stack.pop()[len("link:"):]
            self._inline_stack[-1].append(Link(children, url))
        elif tag in ("ul", "ol"):
            if self._list_stack:
                self._list_stack.pop()
        elif tag == "li":
            children = self._inline_stack.pop()
            ordered = self._list_stack[-1] if self._list_stack else False
            level = max(len(self._list_stack), 1)
            self.blocks.append(ListItem(ordered, level, children))
        elif tag == "blockquote":
            children = self._inline_stack.pop()
            self.blocks.append(Blockquote(children))
        elif tag in ("td", "th"):
            children = self._inline_stack.pop()
            if self._row is not None:
                self._row.append(children)
        elif tag == "tr":
            if self._table is not None:
                if self._table["header"] is None:
                    self._table["header"] = self._row or []
                else:
                    self._table["rows"].append(self._row or [])
            self._row = None
        elif tag == "table":
            if self._table is not None:
                self.blocks.append(Table(self._table["header"] or [], self._table["rows"]))
            self._table = None

    def handle_data(self, data):
        if self._pre_depth > 0:
            if self._code_buffer is not None:
                self._code_buffer.append(data)
            return
        if not data:
            return
        if self._format_stack and self._format_stack[-1] == "code":
            self._inline_stack[-1].append(Code(data))
            return
        self._inline_stack[-1].append(self._wrap_with_open_formats(Text(data)))

    def close(self):
        super().close()
        self._flush_top_paragraph()


def parse_html(text):
    collector = _BlockCollector()
    collector.feed(text)
    collector.close()
    return collector.blocks
