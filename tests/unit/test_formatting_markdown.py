"""Unit tests for the Markdown -> AST parser (no UNO dependency)."""

import unittest

from src.mirai.formatting.ast_nodes import (
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
from src.mirai.formatting.markdown_parser import parse_markdown


class TestPlainText(unittest.TestCase):
    def test_plain_prose_is_a_single_paragraph(self):
        blocks = parse_markdown("Ceci est un texte simple sans formatage.")
        self.assertEqual(len(blocks), 1)
        self.assertIsInstance(blocks[0], Paragraph)
        self.assertEqual(blocks[0].lines, [[Text("Ceci est un texte simple sans formatage.")]])

    def test_blank_lines_separate_paragraphs(self):
        blocks = parse_markdown("Premier paragraphe.\n\nDeuxième paragraphe.")
        self.assertEqual(len(blocks), 2)
        self.assertIsInstance(blocks[0], Paragraph)
        self.assertIsInstance(blocks[1], Paragraph)

    def test_single_newline_becomes_soft_line_break_within_paragraph(self):
        blocks = parse_markdown("Ligne 1\nLigne 2")
        self.assertEqual(len(blocks), 1)
        self.assertEqual(len(blocks[0].lines), 2)


class TestHeadings(unittest.TestCase):
    def test_h1_h2_h3(self):
        blocks = parse_markdown("# Titre\n## Sous-titre\n### Sous-sous-titre")
        self.assertEqual([b.level for b in blocks], [1, 2, 3])
        for b in blocks:
            self.assertIsInstance(b, Heading)

    def test_heading_text_is_parsed_for_inline_markup(self):
        blocks = parse_markdown("# Titre **important**")
        self.assertEqual(blocks[0].inlines, [Text("Titre "), Bold([Text("important")])])


class TestInlineEmphasis(unittest.TestCase):
    def test_bold_star(self):
        blocks = parse_markdown("du **gras** ici")
        self.assertEqual(blocks[0].lines[0], [Text("du "), Bold([Text("gras")]), Text(" ici")])

    def test_italic_star(self):
        blocks = parse_markdown("de l'*italique* ici")
        self.assertEqual(blocks[0].lines[0], [Text("de l'"), Italic([Text("italique")]), Text(" ici")])

    def test_bold_italic(self):
        blocks = parse_markdown("***important***")
        self.assertEqual(blocks[0].lines[0], [Bold([Italic([Text("important")])])])

    def test_inline_code(self):
        blocks = parse_markdown("utilise `git status`")
        self.assertEqual(blocks[0].lines[0], [Text("utilise "), Code("git status")])

    def test_link(self):
        blocks = parse_markdown("voir [le site](https://example.com)")
        self.assertEqual(
            blocks[0].lines[0],
            [Text("voir "), Link([Text("le site")], "https://example.com")],
        )

    def test_snake_case_identifiers_are_not_treated_as_italic(self):
        blocks = parse_markdown("la variable snake_case_variable est définie")
        self.assertEqual(blocks[0].lines[0], [Text("la variable snake_case_variable est définie")])


class TestLists(unittest.TestCase):
    def test_unordered_list(self):
        blocks = parse_markdown("- premier\n- deuxième\n- troisième")
        self.assertEqual(len(blocks), 3)
        for b in blocks:
            self.assertIsInstance(b, ListItem)
            self.assertFalse(b.ordered)

    def test_ordered_list(self):
        blocks = parse_markdown("1. premier\n2. deuxième")
        self.assertTrue(all(isinstance(b, ListItem) and b.ordered for b in blocks))

    def test_nested_list_level(self):
        blocks = parse_markdown("- top\n  - nested")
        self.assertEqual(blocks[0].level, 1)
        self.assertEqual(blocks[1].level, 2)


class TestBlockquote(unittest.TestCase):
    def test_single_line_quote(self):
        blocks = parse_markdown("> une citation")
        self.assertIsInstance(blocks[0], Blockquote)

    def test_multiline_quote_merged(self):
        blocks = parse_markdown("> ligne 1\n> ligne 2")
        self.assertEqual(len(blocks), 1)
        self.assertIsInstance(blocks[0], Blockquote)


class TestCodeBlock(unittest.TestCase):
    def test_fenced_code_block(self):
        blocks = parse_markdown("```\nprint('hello')\n```")
        self.assertEqual(len(blocks), 1)
        self.assertIsInstance(blocks[0], CodeBlock)
        self.assertEqual(blocks[0].text, "print('hello')")


class TestTable(unittest.TestCase):
    def test_simple_table(self):
        md = "| Nom | Âge |\n| --- | --- |\n| Alice | 30 |\n| Bob | 25 |"
        blocks = parse_markdown(md)
        self.assertEqual(len(blocks), 1)
        table = blocks[0]
        self.assertIsInstance(table, Table)
        self.assertEqual(table.header, [[Text("Nom")], [Text("Âge")]])
        self.assertEqual(len(table.rows), 2)
        self.assertEqual(table.rows[0], [[Text("Alice")], [Text("30")]])


class TestHorizontalRule(unittest.TestCase):
    def test_hr_is_dropped_not_left_as_text(self):
        blocks = parse_markdown("Avant\n\n---\n\nAprès")
        self.assertEqual(len(blocks), 2)
        self.assertNotIn("---", "".join(t.value for b in blocks for line in b.lines for t in line))


if __name__ == "__main__":
    unittest.main()
