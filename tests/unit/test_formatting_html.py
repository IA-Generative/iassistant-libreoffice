"""Unit tests for the HTML -> AST parser (no UNO dependency)."""

import unittest

from src.mirai.formatting.ast_nodes import (
    Blockquote,
    Bold,
    Heading,
    Italic,
    Link,
    ListItem,
    Paragraph,
    Table,
    Text,
)
from src.mirai.formatting.html_parser import parse_html
from src.mirai.formatting.insert import looks_like_html


class TestFormatDetection(unittest.TestCase):
    def test_plain_prose_is_not_html(self):
        self.assertFalse(looks_like_html("Un texte tout à fait normal."))

    def test_markdown_is_not_html(self):
        self.assertFalse(looks_like_html("# Titre\n\nDu **gras** et une [lien](url)."))

    def test_html_tags_are_detected(self):
        self.assertTrue(looks_like_html("<h1>Titre</h1><p>Du <strong>gras</strong>.</p>"))


class TestHeadingsAndParagraphs(unittest.TestCase):
    def test_heading_levels(self):
        blocks = parse_html("<h1>Un</h1><h2>Deux</h2>")
        self.assertEqual([b.level for b in blocks], [1, 2])
        for b in blocks:
            self.assertIsInstance(b, Heading)

    def test_paragraph_with_bold_and_italic(self):
        blocks = parse_html("<p>Du <strong>gras</strong> et de l'<em>italique</em>.</p>")
        self.assertEqual(len(blocks), 1)
        self.assertIsInstance(blocks[0], Paragraph)
        inlines = blocks[0].lines[0]
        self.assertIn(Bold([Text("gras")]), inlines)
        self.assertIn(Italic([Text("italique")]), inlines)

    def test_center_alignment_from_style_attribute(self):
        blocks = parse_html('<p style="text-align:center">Centré</p>')
        self.assertEqual(blocks[0].align, "center")


class TestListsAndQuotes(unittest.TestCase):
    def test_unordered_list(self):
        blocks = parse_html("<ul><li>un</li><li>deux</li></ul>")
        self.assertEqual(len(blocks), 2)
        self.assertTrue(all(isinstance(b, ListItem) and not b.ordered for b in blocks))

    def test_ordered_list(self):
        blocks = parse_html("<ol><li>un</li><li>deux</li></ol>")
        self.assertTrue(all(isinstance(b, ListItem) and b.ordered for b in blocks))

    def test_blockquote(self):
        blocks = parse_html("<blockquote>une citation</blockquote>")
        self.assertIsInstance(blocks[0], Blockquote)


class TestLinksAndTables(unittest.TestCase):
    def test_link(self):
        blocks = parse_html('<p>voir <a href="https://example.com">le site</a></p>')
        link = blocks[0].lines[0][-1]
        self.assertIsInstance(link, Link)
        self.assertEqual(link.url, "https://example.com")

    def test_table(self):
        html = (
            "<table><tr><th>Nom</th><th>Âge</th></tr>"
            "<tr><td>Alice</td><td>30</td></tr></table>"
        )
        blocks = parse_html(html)
        self.assertEqual(len(blocks), 1)
        table = blocks[0]
        self.assertIsInstance(table, Table)
        self.assertEqual(len(table.rows), 1)


if __name__ == "__main__":
    unittest.main()
