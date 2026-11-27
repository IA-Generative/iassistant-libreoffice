#!/usr/bin/env python3
"""Produit docs/RECETTE.ods, le classeur à remplir, à partir de docs/RECETTE.md.

docs/RECETTE.md est la source : le classeur se régénère après chaque
modification. Le script s'arrête sans toucher au classeur si le nombre de
scénarios lus diffère du récapitulatif, ou si un texte du Markdown manque.

Usage : python3 scripts/recette_ods.py
Il faut LibreOffice avec Calc, et un Python qui importe `uno` (python3-uno).
"""
import os
import re
import shutil
import subprocess
import tempfile
import time
import zipfile
from collections import Counter
from contextlib import contextmanager
from functools import partial
from pathlib import Path
from typing import NamedTuple
from xml.etree import ElementTree

import uno
from com.sun.star.beans import PropertyValue
from com.sun.star.connection import NoConnectException
from com.sun.star.table import BorderLine2, CellRangeAddress

DOCS = Path(__file__).resolve().parent.parent / "docs"

BLUE, PALE_BLUE, WHITE, GREY = 0x000091, 0xE3E3FD, 0xFFFFFF, 0xEEEEEE
YELLOW, BORDER = 0xFFF2CC, 0xB0B0B0

STEP_LABELS = {"Préparez": "Préparez : ", "Faites": "",
               "Faites ensuite": "Ensuite : ", "Faites enfin": "Enfin : "}
ACTION = re.compile(rf"\*\*({'|'.join(STEP_LABELS)}) :\*\*\s*(.*)", re.DOTALL)
EXPECTED = re.compile(r"\*\*Vous devez voir :\*\*\s*(.*)", re.DOTALL)
SOURCE_LABEL = re.compile(rf"^({'|'.join(STEP_LABELS)}|Vous devez voir) :\s*")

HELP = ("**Comment remplir ce classeur** : dans l'onglet **Scénarios**, pour chaque scénario, "
        "choisissez **Conforme** ou **Non conforme** dans la liste de la colonne **Résultat** "
        "et notez ce que vous avez vu dans **Observations** (cases jaunes). Quand le résultat "
        "d'un scénario est en gris, remplissez les lignes → placées dessous : il se calcule "
        "seul. ★ signale un scénario prioritaire. Dans l'onglet **Récapitulatif**, les totaux "
        "se calculent seuls ; remplissez-y seulement les cases jaunes.")

TEXT_NS = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
TABLE_NS = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"


class Scenario(NamedTuple):
    number: str
    title: str
    part: str
    steps: list
    checks: list


class Document(NamedTuple):
    title: str
    preamble: list
    before: dict
    install: Scenario
    parts: list  # [(titre, blocs d'introduction, [Scenario])]
    summary: list
    trouble_title: str
    trouble: list

    def scenarios(self):
        return [self.install] + [s for _, _, scenarios in self.parts for s in scenarios]


def block_of(lines):
    first = lines[0]
    if first.startswith("#"):
        level = len(first) - len(first.lstrip("#"))
        return ("h", level, first.lstrip("# "))
    if first.startswith(">"):
        return ("quote", " ".join(line.lstrip("> ") for line in lines))
    if first.startswith("|"):
        rows = [[c.strip() for c in line.strip().strip("|").split("|")] for line in lines]
        return ("table", [row for row in rows if not set("".join(row)) <= set("-: ")])
    if re.match(r"(- |\d+\. )", first):
        return ("list", list_items(lines))
    return ("p", " ".join(line.strip() for line in lines))


def list_items(lines):
    items = []
    for line in lines:
        if re.match(r"(- |\d+\. )", line):
            items.append(re.sub(r"^- ", "• ", line))
        else:
            items[-1] += " " + line.strip()
    return items


def parse_blocks(markdown):
    chunks = ([line for line in chunk.split("\n") if line.strip()]
              for chunk in re.split(r"\n\s*\n", markdown))
    return [block_of(lines) for lines in chunks if lines and lines != ["---"]]


def sections(blocks, level):
    """Découpe aux titres de niveau `level` : [(titre ou None, blocs)]."""
    result = [(None, [])]
    for block in blocks:
        if block[0] == "h" and block[1] == level:
            result.append((block[2], []))
        else:
            result[-1][1].append(block)
    return result


def as_text(block):
    return "\n".join(block[1]) if block[0] == "list" else block[1]


def capitalized(text):
    return re.sub(r"^(\**)(.)", lambda m: m[1] + m[2].upper(), text)


def runs(markdown):
    """Markdown en ligne : [(texte, gras, italique, code)]."""
    result, bold, italic = [], False, False
    for token in re.split(r"(`[^`]*`|\*\*|\*)", markdown):
        if token == "**":
            bold = not bold
        elif token == "*":
            italic = not italic
        elif token:
            code = token.startswith("`")
            result.append((token[1:-1] if code else token, bold, italic, code))
    return result


def parse_steps(body):
    """Rend (étapes, tableau) ; une étape est {do, see} ou {note}."""
    steps, todo, table, target = [], [], None, None
    for block in body:
        if block[0] == "table":
            table = block[1]
        elif block[0] == "quote":
            steps.append({"note": block[1]})
        elif not as_text(block).startswith(("☐", "*Observations")):
            text = as_text(block)
            expected, action = EXPECTED.match(text), ACTION.match(text)
            if expected:
                steps.append({"do": todo,
                              "see": [capitalized(expected[1])] if expected[1] else []})
                todo, target = [], steps[-1]["see"]
            elif action:
                prefix = STEP_LABELS[action[1]]
                todo.append(prefix + action[2] if prefix else capitalized(action[2]))
                target = todo
            else:
                target.append(text)
    if todo:
        steps.append({"do": todo, "see": ["Voir les lignes → ci-dessous."] if table else []})
    return steps, table


def sub_checks(table):
    """Cases à cocher d'un tableau de scénario : [(libellé, à faire, à voir)]."""
    if table is None:
        return []
    header, *rows = table
    if {"Writer", "Calc"} <= set(header):
        return [(row[0], f"Dans {app}", row[1]) for row in rows for app in ("Writer", "Calc")]
    return [(row[0], "", "") for row in rows]


def scenario(number, title, part, body):
    steps, table = parse_steps(body)
    return Scenario(number, title, part, steps, sub_checks(table))


def install_scenario(blocks, part):
    numbered = "\n".join(next(block for block in blocks if block[0] == "list")[1])
    body = [("p", "**Faites :** " + numbered),
            ("p", "**Vous devez voir :** un message confirme que l'extension est installée.")]
    return scenario("—", "Installer l'extension", part, body)


def parse_part(title, blocks, part):
    intro, *scenarios = sections(blocks, 3)
    return (title, intro[1],
            [scenario(*heading.split(" ", 1), part, body) for heading, body in scenarios])


def parse_document(markdown):
    preamble, *top = sections(parse_blocks(markdown), 2)
    named = dict(top)
    before = dict(sections(named["Avant de commencer"], 3)[1:])
    summary = named["Récapitulatif"]
    # L'installation compte avec la partie 1 : les lignes du récapitulatif
    # suivent l'ordre des parties 1 à 9.
    part_names = [row[0] for row in summary_table(summary)[1:-1]]
    parts = [parse_part(title, blocks, part_names[int(title.split()[0]) - 1])
             for title, blocks in top if re.match(r"[1-9] —", title)]
    trouble_title = next(title for title in named if title.startswith("10 —"))
    return Document(
        title=next(block[2] for block in preamble[1] if block[0] == "h"),
        preamble=[block for block in preamble[1] if block[0] != "h"],
        before=before,
        install=install_scenario(before["Installer l'extension"], part_names[0]),
        parts=parts,
        summary=summary,
        trouble_title=trouble_title,
        trouble=named[trouble_title])


def summary_table(summary):
    return next(block for block in summary if block[0] == "table")[1]


def summary_paragraph(summary, start):
    return next(block[1] for block in summary if block[0] == "p" and block[1].startswith(start))


def check_counts(document):
    announced = {row[0]: int(row[1]) for row in summary_table(document.summary)[1:-1]}
    found = dict(Counter(s.part for s in document.scenarios()))
    if found != announced:
        raise SystemExit(f"Le récapitulatif annonce {announced}, le script a lu {found}.")


def priority_numbers(paragraph, numbers):
    """Numéros cités en gras : « 7.1 », « 1.1 à 1.3 », ou « 9 » pour toute une partie."""
    cited = set()
    for part, first, last in re.findall(r"\*\*(\d+)(?:\.(\d+))?(?: à \d+\.(\d+))?\*\*", paragraph):
        if first:
            cited |= {f"{part}.{i}" for i in range(int(first), int(last or first) + 1)}
        else:
            cited |= {number for number in numbers if number.split(".")[0] == part}
    return cited


def prop(name, value):
    return PropertyValue(Name=name, Value=value)


class Sheet:
    def __init__(self, sheet, scratch, widths):
        self.sheet, self.scratch, self.widths = sheet, scratch, widths
        for index, width in enumerate(widths):
            sheet.Columns.getByIndex(index).Width = width

    def range(self, col, row, last_col=None, last_row=None):
        if last_col is None and last_row is None:
            return self.sheet.getCellByPosition(col, row)
        return self.sheet.getCellRangeByPosition(
            col, row, col if last_col is None else last_col, row if last_row is None else last_row)

    def style(self, target, size=10, bold=False, italic=False, fill=None, color=0, center=False):
        target.CharFontName = "Arial"
        target.CharHeight = size
        target.CharWeight = 150 if bold else 100
        target.CharPosture = slant(italic)
        target.CharColor = color
        target.IsTextWrapped = True
        target.VertJustify = 1
        for side in ("Left", "Right"):
            setattr(target, f"Para{side}Margin", 150)
        for side in ("Top", "Bottom"):
            setattr(target, f"Para{side}Margin", 80)
        if center:
            target.HoriJustify = uno.Enum("com.sun.star.table.CellHoriJustify", "CENTER")
        if fill is not None:
            target.CellBackColor = fill

    def put(self, col, row, markdown, **style):
        cell = self.range(col, row)
        self.style(cell, **style)
        cell.setString("")
        text = cell.getText()
        cursor = text.createTextCursor()
        for chunk, bold, italic, code in runs(markdown):
            cursor.gotoEnd(False)
            cursor.CharWeight = 150 if bold or style.get("bold") else 100
            cursor.CharPosture = slant(italic or style.get("italic"))
            cursor.CharFontName = "Courier New" if code else "Arial"
            text.insertString(cursor, chunk, False)

    def height_of(self, markdown, width, **style):
        """Calc n'ajuste pas la hauteur d'une cellule fusionnée : la mesurer à part."""
        Sheet(self.scratch, self.scratch, [width]).put(0, 0, markdown, **style)
        row = self.scratch.Rows.getByIndex(0)
        row.OptimalHeight = False
        row.OptimalHeight = True
        return row.Height

    def merged(self, first_col, last_col, row, markdown, **style):
        width = sum(self.widths[first_col:last_col + 1])
        height = self.height_of(markdown, width, **style)
        self.range(first_col, row, last_col).merge(True)
        self.style(self.range(first_col, row, last_col), **style)
        self.put(first_col, row, markdown, **style)
        self.sheet.Rows.getByIndex(row).Height = height + 100

    def banner(self, row, markdown, **style):
        self.merged(0, len(self.widths) - 1, row, markdown, **style)

    def fit(self, row):
        line = self.sheet.Rows.getByIndex(row)
        line.OptimalHeight = False
        line.OptimalHeight = True
        line.Height = line.Height + 100

    def at_least(self, first_row, last_row, height):
        actual = sum(self.sheet.Rows.getByIndex(r).Height for r in range(first_row, last_row + 1))
        if height > actual:
            line = self.sheet.Rows.getByIndex(last_row)
            line.Height = line.Height + height - actual

    def borders(self, first_row, last_row, last_col):
        line = BorderLine2(Color=BORDER, OuterLineWidth=18, LineWidth=18)
        for row in range(first_row, last_row + 1):
            for col in range(last_col + 1):
                cell = self.range(col, row)
                for side in ("TopBorder", "BottomBorder", "LeftBorder", "RightBorder"):
                    setattr(cell, side, line)


def slant(italic):
    return uno.Enum("com.sun.star.awt.FontSlant", "ITALIC" if italic else "NONE")


def restrict_to_verdicts(cell):
    rule = cell.Validation
    rule.Type = uno.Enum("com.sun.star.sheet.ValidationType", "LIST")
    rule.setFormula1('"Conforme";"Non conforme"')
    rule.ShowList = 1
    rule.IgnoreBlankCells = True
    rule.ShowErrorMessage = True
    rule.ErrorAlertStyle = uno.Enum("com.sun.star.sheet.ValidationAlertStyle", "STOP")
    rule.ErrorMessage = "Choisissez « Conforme » ou « Non conforme » dans la liste."
    cell.Validation = rule


def verdict_cell(sheet, row, formula=None):
    cell = sheet.range(4, row)
    sheet.style(cell, bold=True, center=True, fill=GREY if formula else YELLOW)
    if formula:
        cell.setFormula(formula)
    else:
        restrict_to_verdicts(cell)
    conditions = cell.ConditionalFormat
    for value, style_name in (("Conforme", "Conforme"), ("Non conforme", "NonConforme")):
        conditions.addNew((
            prop("Operator", uno.Enum("com.sun.star.sheet.ConditionOperator", "EQUAL")),
            prop("Formula1", f'"{value}"'),
            prop("StyleName", style_name),
        ))
    cell.ConditionalFormat = conditions


def derived_verdict(first_row, count):
    cells = f"E{first_row + 1}:E{first_row + count}"
    return (f'=IF(COUNTIF({cells};"Non conforme")>0;"Non conforme";'
            f'IF(COUNTIF({cells};"Conforme")={count};"Conforme";""))')


def add_step(sheet, row, step):
    if "note" in step:
        sheet.merged(2, 3, row, step["note"], italic=True)
        return
    todo = "\n".join(step["do"]) or "(Consigne commune, en tête de la partie.)"
    sheet.put(2, row, todo, italic=not step["do"])
    sheet.put(3, row, "\n".join(step["see"]))
    sheet.fit(row)


def add_check(sheet, row, check):
    name, todo, expected = check
    if todo:
        sheet.put(1, row, f"→ {name}")
        sheet.put(2, row, todo)
        sheet.put(3, row, expected)
    else:
        sheet.merged(1, 3, row, f"→ {name}")
    verdict_cell(sheet, row)
    sheet.style(sheet.range(5, row), fill=YELLOW)
    sheet.fit(row)


def add_scenario(sheet, first, item, priority):
    for offset, step in enumerate(item.steps):
        add_step(sheet, first + offset, step)
    after_steps = first + len(item.steps)
    last = after_steps - 1
    title_height = sheet.height_of(item.title, sheet.widths[1], bold=True) + 100
    sheet.at_least(first, last, max(title_height, 1100))

    label = f"{item.number} ★" if item.number in priority else item.number
    sheet.put(0, first, label, bold=True, center=True)
    sheet.put(1, first, item.title, bold=True)
    formula = derived_verdict(after_steps, len(item.checks)) if item.checks else None
    verdict_cell(sheet, first, formula)
    sheet.style(sheet.range(5, first), fill=YELLOW)
    # La colonne masquée sert aux totaux du récapitulatif : les cases à cocher
    # n'y figurent pas, un scénario compte donc une fois.
    sheet.range(6, first).setString(item.part)
    if last > first:
        for col in (0, 1, 4, 5):
            sheet.range(col, first, col, last).merge(True)

    for offset, check in enumerate(item.checks):
        add_check(sheet, after_steps + offset, check)
    end = after_steps + len(item.checks)
    sheet.borders(first, end - 1, 5)
    return end


def fill_scenarios(sheet, document, priority):
    headers = ["N°", "Scénario", "Ce qu'il faut faire", "Ce que vous devez voir",
               "Résultat", "Observations", "Partie"]
    for col, title in enumerate(headers):
        sheet.put(col, 0, title, bold=True, fill=BLUE, color=WHITE)
    sheet.fit(0)
    sheet.borders(0, 0, 5)

    heading = {"size": 12, "bold": True, "fill": BLUE, "color": WHITE}
    sheet.banner(1, "Avant de commencer", **heading)
    row = add_scenario(sheet, 2, document.install, priority)
    for title, intro, scenarios in document.parts:
        sheet.banner(row, title, **heading)
        row += 1
        for block in intro:
            sheet.banner(row, as_text(block), italic=block[0] == "quote", fill=PALE_BLUE)
            row += 1
        for item in scenarios:
            row = add_scenario(sheet, row, item, priority)
    sheet.sheet.Columns.getByIndex(6).IsVisible = False
    return row


def add_table(sheet, row, rows):
    for index, cells in enumerate(rows):
        for col, value in enumerate(cells):
            sheet.put(col, row, value, bold=index == 0, fill=PALE_BLUE if index == 0 else None)
        sheet.fit(row)
        sheet.borders(row, row, 1)
        row += 1
    return row + 1


def fill_text(sheet, blocks, row):
    """Titres, texte, listes et tableaux à deux colonnes."""
    for block in blocks:
        if block[0] == "table":
            row = add_table(sheet, row, block[1])
        elif block[0] == "list":
            for item in block[1]:
                sheet.banner(row, item)
                row += 1
        else:
            if block[0] == "h":
                sheet.banner(row, block[2], size=12 if block[1] == 3 else 14,
                             bold=True, color=BLUE)
            elif block[0] == "quote":
                sheet.banner(row, block[1], italic=True, fill=PALE_BLUE)
            else:
                sheet.banner(row, block[1])
            row += 1
    return row


def fill_intro(sheet, document):
    sheet.banner(0, document.title, size=16, bold=True, color=BLUE)
    row = fill_text(sheet, document.preamble, 1)
    sheet.banner(row, HELP, fill=YELLOW)
    blocks = [("h", 2, "Avant de commencer"),
              ("h", 3, "Ce qu'il vous faut"), *document.before["Ce qu'il vous faut"],
              ("h", 3, "Installer l'extension"),
              ("p", "C'est la première ligne de l'onglet **Scénarios**."),
              ("h", 3, "Comment ouvrir l'assistant"),
              *document.before["Comment ouvrir l'assistant"]]
    fill_text(sheet, blocks, row + 2)


def fill_totals(sheet, table, last_scenario_row):
    for col, title in enumerate(table[0]):
        sheet.put(col, 0, title, bold=True, fill=BLUE, color=WHITE)
    sheet.fit(0)
    keys = f"$'Scénarios'.$G$2:$G${last_scenario_row}"
    verdicts = f"$'Scénarios'.$E$2:$E${last_scenario_row}"
    names = [row[0] for row in table[1:-1]]
    for row, name in enumerate(names, start=1):
        sheet.put(0, row, name)
        formulas = [f"=COUNTIF({keys};A{row + 1})",
                    f'=COUNTIFS({keys};A{row + 1};{verdicts};"Conforme")',
                    f'=COUNTIFS({keys};A{row + 1};{verdicts};"Non conforme")']
        for col, formula in enumerate(formulas, start=1):
            sheet.style(sheet.range(col, row), center=True)
            sheet.range(col, row).setFormula(formula)
        sheet.fit(row)
    total = len(names) + 1
    sheet.put(0, total, "Total", bold=True, fill=PALE_BLUE)
    for col, letter in enumerate("BCD", start=1):
        sheet.style(sheet.range(col, total), bold=True, center=True, fill=PALE_BLUE)
        sheet.range(col, total).setFormula(f"=SUM({letter}2:{letter}{total})")
    sheet.fit(total)
    sheet.borders(0, total, 3)
    return total + 1


def fill_summary(sheet, summary, last_scenario_row):
    row = fill_totals(sheet, summary_table(summary), last_scenario_row) + 1
    for label in ("Testeur", "Date", "Système (Windows, macOS ou Linux)",
                  "Version de LibreOffice"):
        sheet.put(0, row, label, bold=True)
        sheet.range(1, row, 3).merge(True)
        sheet.style(sheet.range(1, row, 3), fill=YELLOW)
        sheet.fit(row)
        sheet.borders(row, row, 3)
        row += 1
    sheet.banner(row + 1, summary_paragraph(summary, "**Les scénarios prioritaires"),
                 fill=PALE_BLUE)
    sheet.banner(row + 3, summary_paragraph(summary, "**Impression générale"))
    sheet.range(0, row + 4, 3).merge(True)
    sheet.style(sheet.range(0, row + 4, 3), fill=YELLOW)
    sheet.sheet.Rows.getByIndex(row + 4).Height = 3000
    sheet.borders(row + 4, row + 4, 3)


def define_styles(workbook):
    styles = workbook.StyleFamilies.getByName("CellStyles")
    for name, fill, color in (("Conforme", 0xB8E6C1, 0x0F5223),
                              ("NonConforme", 0xF7C4C0, 0x8A1008)):
        style = workbook.createInstance("com.sun.star.style.CellStyle")
        styles.insertByName(name, style)
        style.CellBackColor = fill
        style.CharColor = color
        style.CharWeight = 150
    page = workbook.StyleFamilies.getByName("PageStyles").getByName("Default")
    page.IsLandscape = True
    page.Width, page.Height = 29700, 21000
    for side in ("Left", "Right", "Top", "Bottom"):
        setattr(page, f"{side}Margin", 1000)
    page.ScaleToPagesX = 1
    page.ScaleToPagesY = 0


def set_up_views(workbook, last_scenario_row):
    detail = workbook.Sheets.getByName("Scénarios")
    header = CellRangeAddress(Sheet=1, StartColumn=0, StartRow=0, EndColumn=5, EndRow=0)
    detail.setTitleRows(header)
    detail.setPrintTitleRows(True)
    detail.setPrintAreas((CellRangeAddress(Sheet=1, StartColumn=0, StartRow=0,
                                           EndColumn=5, EndRow=last_scenario_row - 1),))
    view = workbook.CurrentController
    view.setActiveSheet(detail)
    view.freezeAtPosition(0, 1)
    view.setActiveSheet(workbook.Sheets.getByIndex(0))


def write_workbook(desktop, document, target):
    priority = priority_numbers(
        summary_paragraph(document.summary, "**Les scénarios prioritaires"),
        [item.number for item in document.scenarios()])
    workbook = desktop.loadComponentFromURL("private:factory/scalc", "_blank", 0,
                                            (prop("Hidden", True),))
    define_styles(workbook)
    sheets = workbook.Sheets
    sheets.getByIndex(0).Name = "Avant de commencer"
    names = ("Scénarios", "Récapitulatif", "En cas de problème", "mesure")
    for index, name in enumerate(names, start=1):
        sheets.insertNewByName(name, index)
    scratch = sheets.getByName("mesure")

    def sheet(name, widths):
        return Sheet(sheets.getByName(name), scratch, widths)

    last_row = fill_scenarios(sheet("Scénarios", [1500, 4600, 7400, 9200, 2800, 5600]),
                              document, priority)
    fill_intro(sheet("Avant de commencer", [5000, 20000]), document)
    fill_summary(sheet("Récapitulatif", [7000, 3500, 3500, 3500]), document.summary, last_row)
    trouble = sheet("En cas de problème", [7000, 18000])
    trouble.banner(0, document.trouble_title, size=14, bold=True, color=BLUE)
    fill_text(trouble, document.trouble, 1)

    sheets.removeByName("mesure")
    set_up_views(workbook, last_row)
    workbook.storeToURL(target.as_uri(), (prop("FilterName", "calc8"),))
    workbook.close(True)


def soffice_binary():
    launcher = shutil.which("soffice")
    if not launcher:
        raise SystemExit("soffice introuvable : installez LibreOffice avec Calc.")
    return str(Path(launcher).resolve().parent / "soffice.bin")


@contextmanager
def headless_desktop(profile):
    """Un LibreOffice sans fenêtre, sur un profil jetable."""
    pipe = f"recette-ods-{os.getpid()}"
    command = [soffice_binary(), "--headless", "--norestore",
               f"-env:UserInstallation={profile.as_uri()}", f"--accept=pipe,name={pipe};urp;"]
    local = uno.getComponentContext()
    resolver = local.ServiceManager.createInstanceWithContext(
        "com.sun.star.bridge.UnoUrlResolver", local)
    # TMPDIR : les fichiers temporaires de LibreOffice partent avec le dossier de travail.
    start = partial(subprocess.Popen, command, stderr=subprocess.DEVNULL,
                    env={**os.environ, "TMPDIR": str(profile.parent)})
    server = start()
    try:
        for _ in range(180):
            # Code 81 : LibreOffice a créé son profil et demande à être relancé.
            if server.poll() == 81:
                server = start()
            try:
                context = resolver.resolve(
                    f"uno:pipe,name={pipe};urp;StarOffice.ComponentContext")
                break
            except NoConnectException:
                time.sleep(0.5)
        else:
            raise SystemExit("LibreOffice n'a pas ouvert sa liaison UNO en 90 secondes.")
        yield context.ServiceManager.createInstanceWithContext(
            "com.sun.star.frame.Desktop", context)
    finally:
        server.terminate()
        server.wait(timeout=30)


def flatten(node):
    if node.tag == f"{TEXT_NS}s":
        return " " * int(node.get(f"{TEXT_NS}c", 1))
    if node.tag in (f"{TEXT_NS}line-break", f"{TEXT_NS}tab"):
        return " "
    return (node.text or "") + "".join(flatten(c) + (c.tail or "") for c in node)


def normalized(text):
    return " ".join(text.split()).lower()


def workbook_text(path):
    with zipfile.ZipFile(path) as archive:
        root = ElementTree.fromstring(archive.read("content.xml"))
    return normalized(" ".join(
        " ".join(flatten(paragraph) for paragraph in cell.findall(f"{TEXT_NS}p"))
        for cell in root.iter(f"{TABLE_NS}table-cell")))


def units_of(block):
    if block[0] == "h":
        return [re.sub(r"^\d+\.\d+ ", "", block[2])]
    if block[0] == "list":
        return block[1]
    if block[0] == "table":
        return [cell for row in block[1] for cell in row]
    return [block[1]]


def source_texts(markdown):
    for block in parse_blocks(markdown):
        for unit in units_of(block):
            plain = SOURCE_LABEL.sub("", "".join(run[0] for run in runs(unit)))
            # Les cases à cocher et les lignes à remplir deviennent des cellules vides.
            if plain and not plain.startswith(("☐", "Observations :")) and "____" not in plain:
                yield plain


def check_nothing_lost(markdown, workbook_path):
    written = workbook_text(workbook_path)
    missing = [text for text in source_texts(markdown) if normalized(text) not in written]
    if missing:
        raise SystemExit("Textes du Markdown absents du classeur :\n- " + "\n- ".join(missing))


def main():
    markdown = (DOCS / "RECETTE.md").read_text(encoding="utf-8")
    document = parse_document(markdown)
    check_counts(document)
    with tempfile.TemporaryDirectory(prefix="recette-ods-") as work:
        draft = Path(work) / "RECETTE.ods"
        with headless_desktop(Path(work) / "profile") as desktop:
            write_workbook(desktop, document, draft)
        check_nothing_lost(markdown, draft)
        shutil.copyfile(draft, DOCS / "RECETTE.ods")
    print(f"docs/RECETTE.ods : {len(document.scenarios())} scénarios")


if __name__ == "__main__":
    main()
