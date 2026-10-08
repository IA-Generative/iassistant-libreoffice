"""Top-level entry point: turn raw LLM text into native Writer content."""

import re

from .html_parser import parse_html
from .markdown_parser import parse_markdown
from .uno_writer import write_blocks

_HTML_BLOCK_TAG_RE = re.compile(
    r"</(p|div|h[1-6]|ul|ol|li|table|tr|td|th|strong|em|b|i|blockquote|a)\s*>"
    r"|<(p|div|h[1-6]|ul|ol|table|blockquote)[\s>]",
    re.IGNORECASE,
)


def looks_like_html(text):
    return bool(_HTML_BLOCK_TAG_RE.search(text))


def insert_formatted(model, text_obj, cursor, raw_text, base_char_style=None, base_para_style=None):
    """Insert *raw_text* (Markdown, HTML, or plain prose) at *cursor* as native
    Writer content: real headings, bold/italic, lists, quotes, links and
    tables instead of literal Markdown/HTML syntax.

    Plain prose with no recognizable markup becomes a single paragraph.
    """
    if not raw_text:
        return
    blocks = parse_html(raw_text) if looks_like_html(raw_text) else parse_markdown(raw_text)
    write_blocks(model, text_obj, cursor, blocks, base_char_style, base_para_style)
