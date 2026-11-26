"""Tools UNO restants : carte du document, find/replace, plages Calc, fill down."""

import pytest

from src.mirai.core.context import ToolContext
from src.mirai.core.registry import ToolRegistry
from src.mirai.core.tools import calc_tools, register_all
from tests.stubs.fake_docs import FakeCalcDoc, FakeCalcSheet, FakeWriterDoc
from tests.stubs.fake_shell import FakeShell


def _writer_ctx(doc):
    return ToolContext(doc, doc.controller, "writer", FakeShell())


def _calc_ctx(doc):
    return ToolContext(doc, doc.controller, "calc", FakeShell())


def _registry():
    return register_all(ToolRegistry())


def test_writer_get_selection():
    doc = FakeWriterDoc(selection_text="texte choisi")
    result = _registry().call_tool("writer_get_selection", {}, _writer_ctx(doc))
    assert result.ok and "texte choisi" in result.content
    assert result.data["char_count"] == 12


def test_writer_get_selection_empty():
    doc = FakeWriterDoc(selection_text="  ")
    result = _registry().call_tool("writer_get_selection", {}, _writer_ctx(doc))
    assert result.ok and "vide" in result.content


def test_writer_document_map_numbered_and_truncated():
    doc = FakeWriterDoc(paragraphs=["Premier paragraphe.", "", "Troisième."])
    result = _registry().call_tool("writer_get_document_map", {}, _writer_ctx(doc))
    assert "[P1] Premier paragraphe." in result.content
    assert "[P2] (vide)" in result.content
    assert "[P3] Troisième." in result.content

    long_doc = FakeWriterDoc(paragraphs=["x" * 400 for _ in range(10)])
    result = _registry().call_tool("writer_get_document_map",
                                   {"max_chars": 900}, _writer_ctx(long_doc))
    assert result.data["truncated"] is True
    assert "tronqué" in result.content


@pytest.mark.parametrize("paragraph_count, config, args", [
    (200, {}, {}),
    (200, {}, {"max_chars": 20000}),
    (200, {"tool_result_max_chars": 1500}, {}),
    (56, {}, {}),
], ids=["defaut", "max_chars_au_dela_du_plafond", "plafond_configure_bas",
        "tient_sans_ses_reperes"])
def test_writer_document_map_large_document_keeps_its_end(paragraph_count, config, args):
    """Sur un grand document, le modèle reçoit encore la fin de la carte.

    Le registre plafonne chaque résultat à `tool_result_max_chars` : la carte
    doit tenir dessous avec ses repères de fin, sans quoi le modèle perd le
    rappel d'étape suivante et ne sait plus jusqu'où il a lu.
    """
    def map_of(paragraphs):
        doc = FakeWriterDoc(paragraphs=paragraphs)
        ctx = ToolContext(doc, doc.controller, "writer", FakeShell(config))
        return _registry().call_tool("writer_get_document_map", args, ctx)

    reminder = map_of(["Court."]).content.splitlines()[-1]
    paragraphs = [f"Paragraphe {i} " + "x" * 85 for i in range(1, paragraph_count + 1)]

    result = map_of(paragraphs)

    assert result.content.endswith(reminder)
    assert result.data["truncated"] is True
    assert "FIN DU DOCUMENT" not in result.content
    assert "tronqué" in result.content


def test_writer_insert_text_after_selection():
    doc = FakeWriterDoc(selection_text="ancre")
    result = _registry().call_tool("writer_insert_text", {"text": "ajout"},
                                   _writer_ctx(doc))
    assert result.ok and doc.inserted == "ajout"


def test_writer_replace_selection():
    doc = FakeWriterDoc(selection_text="ancien")
    ctx = _writer_ctx(doc)
    result = _registry().call_tool("writer_replace_selection",
                                   {"text": "nouveau"}, ctx)
    assert result.ok and doc.replaced == "nouveau"
    assert ctx._undo_open  # tool mutant → contexte undo ouvert (fermé par le run)


def test_writer_find_replace_counts_and_misses():
    doc = FakeWriterDoc(paragraphs=["le chat dort", "le chien court"])
    result = _registry().call_tool(
        "writer_find_replace",
        {"pairs": [{"find": "chat", "replace": "lynx"},
                   {"find": "absent", "replace": "x"}]},
        _writer_ctx(doc))
    assert result.ok
    assert doc.paragraphs[0] == "le lynx dort"
    assert result.data["applied"] == 1
    assert result.data["not_found"] == ["absent"]


def test_calc_read_range():
    sheet = FakeCalcSheet(grid={(0, 0): "a", (1, 0): "b", (0, 1): "c"})
    doc = FakeCalcDoc(sheet)
    result = _registry().call_tool("calc_read_range", {"range": "A1:B2"},
                                   _calc_ctx(doc))
    assert result.ok
    assert result.content == "a | b\nc | "


def test_calc_read_range_gives_each_formula_next_to_the_value_it_shows():
    """Une cellule à formule se lit « valeur (formule =…) », même quand elle
    n'affiche rien ; les autres cellules se lisent telles qu'affichées."""
    sheet = FakeCalcSheet(grid={(0, 0): "Err:502", (1, 0): "10", (0, 1): ""})
    sheet.formulas.update({(0, 0): "=LOG10(C1)", (0, 1): '=IF(B1>50;"élevé";"")'})
    result = _registry().call_tool("calc_read_range", {"range": "A1:B2"},
                                   _calc_ctx(FakeCalcDoc(sheet)))
    assert result.ok
    assert result.content == ('Err:502 (formule =LOG10(C1)) | 10\n'
                              '(formule =IF(B1>50;"élevé";"")) | ')


def test_calc_read_range_gives_the_formula_of_each_adjacent_formula_cell():
    sheet = FakeCalcSheet(grid={(0, 0): "1", (1, 0): "2", (2, 0): "3", (3, 0): "x"})
    sheet.formulas.update({(0, 0): "=0+1", (1, 0): "=A1+1", (2, 0): "=B1+1"})
    result = _registry().call_tool("calc_read_range", {"range": "A1:D1"},
                                   _calc_ctx(FakeCalcDoc(sheet)))
    assert result.content == ("1 (formule =0+1) | 2 (formule =A1+1) | "
                              "3 (formule =B1+1) | x")


def test_calc_read_range_reads_a_text_starting_with_equals_as_text():
    """Un texte saisi « =… » n'est pas une formule : il se lit tel quel."""
    sheet = FakeCalcSheet(grid={(0, 0): "=pas une formule"})
    result = _registry().call_tool("calc_read_range", {"range": "A1"},
                                   _calc_ctx(FakeCalcDoc(sheet)))
    assert result.content == "=pas une formule"


@pytest.mark.parametrize("protected, shown", [
    (True, "12 | 24 (formule =C1*4)"),
    (False, "12 (formule =C1*2) | 24 (formule =C1*4)"),
], ids=["feuille_protegee", "feuille_libre"])
def test_calc_read_range_keeps_a_hidden_formula_hidden_while_the_sheet_is_protected(
        protected, shown):
    """Comme dans Calc, une formule masquée ne se lit que feuille déprotégée ;
    sa valeur reste lisible."""
    sheet = FakeCalcSheet(grid={(0, 0): "12", (1, 0): "24"})
    sheet.formulas.update({(0, 0): "=C1*2", (1, 0): "=C1*4"})
    sheet.hidden_formulas.add((0, 0))
    sheet.protected = protected
    result = _registry().call_tool("calc_read_range", {"range": "A1:B1"},
                                   _calc_ctx(FakeCalcDoc(sheet)))
    assert result.content == shown


def test_calc_read_range_keeps_the_formula_of_a_hide_all_cell_hidden_while_the_sheet_is_protected():
    """« Masquer tout » cache aussi la formule d'une feuille protégée."""
    sheet = FakeCalcSheet(grid={(0, 0): "12", (1, 0): "24"})
    sheet.formulas.update({(0, 0): "=C1*2", (1, 0): "=C1*4"})
    sheet.hidden_cells.add((0, 0))
    sheet.protected = True
    result = _registry().call_tool("calc_read_range", {"range": "A1:B1"},
                                   _calc_ctx(FakeCalcDoc(sheet)))
    assert "=C1*2" not in result.content
    assert result.content.endswith(" | 24 (formule =C1*4)")


def test_calc_read_range_gives_the_formula_of_each_cell_of_a_formula_column():
    sheet = FakeCalcSheet(grid={(0, r): str(2 * r) for r in range(5)})
    sheet.formulas.update({(0, r): f"=B{r + 1}*2" for r in range(5)})
    result = _registry().call_tool("calc_read_range", {"range": "A1:A5"},
                                   _calc_ctx(FakeCalcDoc(sheet)))
    assert result.content.splitlines() == [f"{2 * r} (formule =B{r + 1}*2)" for r in range(5)]


@pytest.mark.parametrize("block_cells", [4, 1], ids=["deux_lignes_par_bloc", "une_ligne_par_bloc"])
def test_calc_read_range_finds_the_formulas_of_every_reading_block(monkeypatch, block_cells):
    """Une grande plage est lue par blocs de lignes : une formule en bord de
    bloc, ou à cheval sur deux, se lit comme les autres."""
    monkeypatch.setattr(calc_tools, "_FORMULA_QUERY_CELLS", block_cells)
    sheet = FakeCalcSheet(grid={(c, r): str(10 * r + c) for c in range(2) for r in range(4)})
    sheet.formulas.update({(0, r): f"=B{r + 1}-1" for r in range(3)})
    sheet.formulas[(1, 2)] = "=A3+1"
    result = _registry().call_tool("calc_read_range", {"range": "A1:B4"},
                                   _calc_ctx(FakeCalcDoc(sheet)))
    assert result.content.splitlines() == [
        "0 (formule =B1-1) | 1",
        "10 (formule =B2-1) | 11",
        "20 (formule =B3-1) | 21 (formule =A3+1)",
        "30 | 31",
    ]


def test_calc_read_range_of_a_long_column_does_not_search_formulas_row_by_row():
    sheet = FakeCalcSheet(grid={(0, r): str(r) for r in range(5000)}, num_rows=10000)
    sheet.formulas.update({(0, r): f"=B{r + 1}" for r in range(0, 5000, 10)})
    result = _registry().call_tool("calc_read_range",
                                   {"range": "A1:A5000", "max_cells": 5000},
                                   _calc_ctx(FakeCalcDoc(sheet)))
    assert result.content.startswith("0 (formule =B1)\n1\n")
    assert sheet.formula_queries < 10


def test_calc_read_range_reads_no_more_cells_than_max_cells():
    sheet = FakeCalcSheet(grid={(c, r): "x" for c in range(10) for r in range(3)})
    result = _registry().call_tool("calc_read_range", {"range": "A1:J3", "max_cells": 15},
                                   _calc_ctx(FakeCalcDoc(sheet)))
    assert result.content == (" | ".join(["x"] * 10) + "\n" + " | ".join(["x"] * 5)
                              + "\n[... tronqué à 15 cellules ...]")
    assert sheet.cells_asked == 15


@pytest.mark.parametrize("name", ["calc_get_selection", "calc_read_range",
                                  "calc_get_sheet_overview"])
def test_calc_read_tools_tell_the_model_how_a_formula_reads(name):
    """Le modèle apprend la forme d'une cellule à formule, et que la formule
    lue est dans la syntaxe que calc_set_formula accepte."""
    spec = next(s for s in _registry().list_tools("calc") if s.name == name)
    assert "(formule =…)" in spec.description
    assert "calc_set_formula" in spec.description


def test_calc_write_cells():
    sheet = FakeCalcSheet()
    doc = FakeCalcDoc(sheet)
    result = _registry().call_tool(
        "calc_write_cells",
        {"cells": [{"ref": "B2", "value": "**gras**"}]}, _calc_ctx(doc))
    assert result.ok
    assert sheet.grid[(1, 1)] == "gras"       # markdown nettoyé


def test_calc_set_formula_reports_error_token():
    sheet = FakeCalcSheet()
    sheet.formula_results["=VLOOKUP(Z1;A1:B2;2;0)"] = "#N/A"
    sheet.formula_errors["=VLOOKUP(Z1;A1:B2;2;0)"] = 32767
    doc = FakeCalcDoc(sheet)
    result = _registry().call_tool(
        "calc_set_formula", {"ref": "C1", "formula": "```\n=VLOOKUP(Z1;A1:B2;2;0)\n```"},
        _calc_ctx(doc))
    assert not result.ok
    assert "#N/A" in result.content
    assert sheet.formulas[(2, 0)] == "=VLOOKUP(Z1;A1:B2;2;0)"   # fences nettoyées


def test_calc_set_formula_reports_calc_error_code():
    sheet = FakeCalcSheet()
    sheet.formula_results["=SQRT(-1)"] = "Err:502"
    sheet.formula_errors["=SQRT(-1)"] = 502
    doc = FakeCalcDoc(sheet)
    result = _registry().call_tool(
        "calc_set_formula", {"ref": "C1", "formula": "=SQRT(-1)"}, _calc_ctx(doc))
    assert not result.ok
    assert result.error == "Err:502"


@pytest.mark.parametrize("formula, shown", [('="#tag"', "#tag"), ('="Err:1"', "Err:1")])
def test_calc_set_formula_accepts_a_text_result_that_looks_like_an_error(formula, shown):
    sheet = FakeCalcSheet()
    sheet.formula_results[formula] = shown
    doc = FakeCalcDoc(sheet)
    result = _registry().call_tool(
        "calc_set_formula", {"ref": "C1", "formula": formula}, _calc_ctx(doc))
    assert result.ok
    assert shown in result.content


def _protected_sheet(grid=None, unlocked=()):
    """Feuille protégée : comme dans Calc, toute cellule y est verrouillée,
    sauf celles que l'auteur du classeur a déverrouillées."""
    sheet = FakeCalcSheet(grid=grid)
    sheet.protected = True
    sheet.unlocked.update(unlocked)
    return sheet


def test_calc_set_formula_leaves_a_locked_cell_of_a_protected_sheet_as_it_was():
    """Calc refuse la saisie dans une cellule verrouillée ; l'assistant aussi,
    et le modèle apprend quelle cellule et pourquoi."""
    sheet = _protected_sheet({(2, 0): "24"})
    sheet.formulas[(2, 0)] = "=A1*2"
    result = _registry().call_tool(
        "calc_set_formula", {"ref": "C1", "formula": "=A1*3"}, _calc_ctx(FakeCalcDoc(sheet)))
    assert not result.ok
    assert result.error == "C1 : cellule verrouillée (feuille protégée)"
    assert sheet.formulas[(2, 0)] == "=A1*2"
    assert sheet.grid[(2, 0)] == "24"


def test_calc_set_formula_leaves_an_unlocked_hide_all_cell_of_a_protected_sheet_as_it_was():
    """Calc refuse aussi la saisie dans une cellule « Masquer tout » d'une
    feuille protégée, même déverrouillée."""
    sheet = _protected_sheet({(2, 0): "24"}, unlocked={(2, 0)})
    sheet.hidden_cells.add((2, 0))
    result = _registry().call_tool(
        "calc_set_formula", {"ref": "C1", "formula": "=A1*3"}, _calc_ctx(FakeCalcDoc(sheet)))
    assert not result.ok
    assert result.error == "C1 : cellule verrouillée (feuille protégée)"
    assert sheet.formulas == {}
    assert sheet.grid[(2, 0)] == "24"


def test_calc_set_formula_applies_in_a_hide_all_cell_of_an_unprotected_sheet():
    sheet = FakeCalcSheet()
    sheet.hidden_cells.add((2, 0))
    sheet.formula_results["=A1*3"] = "36"
    result = _registry().call_tool(
        "calc_set_formula", {"ref": "C1", "formula": "=A1*3"}, _calc_ctx(FakeCalcDoc(sheet)))
    assert result.ok
    assert sheet.formulas[(2, 0)] == "=A1*3"


def test_calc_set_formula_applies_in_an_unlocked_cell_of_a_protected_sheet():
    sheet = _protected_sheet(unlocked={(2, 0)})
    sheet.formula_results["=A1*3"] = "36"
    result = _registry().call_tool(
        "calc_set_formula", {"ref": "C1", "formula": "=A1*3"}, _calc_ctx(FakeCalcDoc(sheet)))
    assert result.ok
    assert sheet.formulas[(2, 0)] == "=A1*3"


def test_calc_write_cells_writes_the_unlocked_cells_and_names_each_locked_one():
    """Sur une feuille protégée, l'assistant écrit là où l'utilisateur peut
    saisir. Un texte à apostrophe initiale ne passe pas davantage."""
    original = {(0, 0): "Titre", (0, 1): "Code"}
    sheet = _protected_sheet(original, unlocked={(1, 0)})
    result = _registry().call_tool(
        "calc_write_cells",
        {"cells": [{"ref": "A1", "value": "Autre titre"},
                   {"ref": "B1", "value": "saisie"},
                   {"ref": "A2", "value": "'0612"}]},
        _calc_ctx(FakeCalcDoc(sheet)))
    assert not result.ok
    assert result.content.startswith("1 cellule(s) écrite(s).")
    assert result.error == ("A1 : cellule verrouillée (feuille protégée); "
                            "A2 : cellule verrouillée (feuille protégée)")
    assert sheet.grid == {**original, (1, 0): "saisie"}


def _fill_down(sheet, to_row):
    return _registry().call_tool(
        "calc_fill_formula_down", {"from_ref": "C2", "to_row": to_row},
        _calc_ctx(FakeCalcDoc(sheet)))


def test_calc_fill_formula_down_fills_rows_without_data_too():
    """Les colonnes voisines encore vides ne retiennent pas la recopie :
    l'utilisateur a demandé la formule jusqu'à to_row."""
    sheet = FakeCalcSheet(grid={(0, 1): "10"})
    sheet.formulas[(2, 1)] = "=A2*2"
    result = _fill_down(sheet, 5)
    assert result.ok
    assert sorted(sheet.formulas) == [(2, 1), (2, 2), (2, 3), (2, 4)]
    assert sheet.formulas[(2, 4)] == "=A5*2"
    assert "3 ligne(s)" in result.content


def test_calc_fill_formula_down_includes_to_row():
    sheet = FakeCalcSheet(grid={(0, r): str(r) for r in range(1, 9)})
    sheet.formulas[(2, 1)] = "=A2*2"
    result = _fill_down(sheet, 4)
    assert result.ok
    assert sorted(sheet.formulas) == [(2, 1), (2, 2), (2, 3)]   # jusqu'à C4


@pytest.mark.parametrize("formula, copied", [
    ("=LOG10(A2)", "=LOG10(A3)"),
    ("=DAYS360(A2;B2)", "=DAYS360(A3;B3)"),
    ("=ATAN2(A2;B2)", "=ATAN2(A3;B3)"),
    ("=COM.SUN.STAR.SHEET.ADDIN.ANALYSIS.GETDEC2HEX(A2)",
     "=COM.SUN.STAR.SHEET.ADDIN.ANALYSIS.GETDEC2HEX(A3)"),
    ('=IF(A2="Q1";1;0)', '=IF(A3="Q1";1;0)'),
    ("=A2*$B$1", "=A3*$B$1"),
    ("=SUM(A$1:A2)", "=SUM(A$1:A3)"),
])
def test_calc_fill_formula_down_shifts_only_relative_rows(formula, copied):
    """La ligne suivante reçoit la formule telle que Calc la décale : noms de
    fonction, littéraux et lignes ancrées par $ intacts."""
    sheet = FakeCalcSheet(grid={(0, 1): "1", (0, 2): "2"})
    sheet.formulas[(2, 1)] = formula
    result = _fill_down(sheet, 3)
    assert result.ok
    assert sheet.formulas[(2, 2)] == copied


@pytest.mark.parametrize("to_row, protected, cause", [
    (2, False, "dépasser"),      # to_row = ligne source
    (5, True, "protégée"),
])
def test_calc_fill_formula_down_refuses_and_names_the_cause(to_row, protected, cause):
    sheet = FakeCalcSheet(grid={(0, 1): "1", (0, 2): "2"})
    sheet.formulas[(2, 1)] = "=A2*2"
    sheet.protected = protected
    result = _fill_down(sheet, to_row)
    assert not result.ok
    assert "C2" in result.error
    assert cause in result.error
    assert list(sheet.formulas) == [(2, 1)]


def test_calc_fill_formula_down_writes_nothing_when_a_target_cell_is_locked():
    """Une recopie à trous tromperait : elle se refuse en entier, et le modèle
    apprend quelles cellules la retiennent."""
    sheet = _protected_sheet({(0, 1): "1"}, unlocked={(2, 2), (2, 4)})
    sheet.formulas[(2, 1)] = "=A2*2"
    result = _fill_down(sheet, 5)
    assert not result.ok
    assert result.error == ("Formule de C2 non recopiée : C3:C5 contient des cellules "
                            "verrouillées (feuille protégée).")
    assert list(sheet.formulas) == [(2, 1)]


def test_calc_fill_formula_down_refuses_when_only_the_last_target_row_is_locked():
    sheet = _protected_sheet({(0, 1): "1"}, unlocked={(2, 2), (2, 3)})
    sheet.formulas[(2, 1)] = "=A2*2"
    result = _fill_down(sheet, 5)
    assert not result.ok
    assert result.error == ("Formule de C2 non recopiée : C3:C5 contient des cellules "
                            "verrouillées (feuille protégée).")
    assert list(sheet.formulas) == [(2, 1)]


def test_calc_fill_formula_down_names_a_single_locked_target_as_one_cell():
    sheet = _protected_sheet({(0, 1): "1"})
    sheet.formulas[(2, 1)] = "=A2*2"
    result = _fill_down(sheet, 3)
    assert not result.ok
    assert result.error == ("Formule de C2 non recopiée : C3 contient des cellules "
                            "verrouillées (feuille protégée).")
    assert sheet.formulas == {(2, 1): "=A2*2"}


@pytest.mark.parametrize("hidden_by", ["hidden_formulas", "hidden_cells"],
                         ids=["masquer_la_formule", "masquer_tout"])
def test_calc_fill_formula_down_does_not_copy_a_formula_the_protection_hides(hidden_by):
    """Une formule que la feuille protégée cache ne se recopie pas dans des
    cellules où elle se lirait, et le refus n'en cite rien."""
    sheet = _protected_sheet({(0, 1): "1"}, unlocked={(2, 2), (2, 3)})
    sheet.formulas[(2, 1)] = "=WEBSERVICE(A2)"
    getattr(sheet, hidden_by).add((2, 1))
    result = _fill_down(sheet, 4)
    assert not result.ok
    assert result.error == ("Formule de C2 non recopiée : la protection de la feuille "
                            "la masque.")
    assert list(sheet.formulas) == [(2, 1)]


def test_calc_fill_formula_down_copies_a_hidden_formula_once_the_sheet_is_unprotected():
    """Comme à la lecture, « Masquer la formule » ne joue que feuille protégée."""
    sheet = FakeCalcSheet(grid={(0, 1): "1"})
    sheet.formulas[(2, 1)] = "=A2*2"
    sheet.hidden_formulas.add((2, 1))
    result = _fill_down(sheet, 3)
    assert result.ok
    assert sheet.formulas[(2, 2)] == "=A3*2"


def test_calc_fill_formula_down_fills_the_unlocked_cells_of_a_protected_sheet():
    sheet = _protected_sheet({(0, 1): "1"}, unlocked={(2, 2), (2, 3), (2, 4)})
    sheet.formulas[(2, 1)] = "=A2*2"
    result = _fill_down(sheet, 5)
    assert result.ok
    assert sheet.formulas == {(2, 1): "=A2*2", (2, 2): "=A3*2", (2, 3): "=A4*2",
                              (2, 4): "=A5*2"}


def test_calc_result_column_of_a_whole_column_scans_only_the_used_rows():
    sheet = FakeCalcSheet(grid={(0, 0): "a", (0, 1): "b"}, num_rows=1048576)
    doc = FakeCalcDoc(sheet, selection_ref="A1:A1048576")
    result = _registry().call_tool("calc_write_result_column",
                                   {"values": ["A", "B"]}, _calc_ctx(doc))
    assert result.ok
    assert (sheet.grid[(1, 0)], sheet.grid[(1, 1)]) == ("A", "B")
    assert sheet.cells_asked < 30


@pytest.mark.parametrize("unlocked", [{(2, 1)}, {(2, 1), (2, 2)}],
                         ids=["cellule_de_resultat", "en_tete"])
def test_calc_result_column_writes_nothing_when_one_of_its_cells_is_locked(unlocked):
    """Une colonne de résultats à trous tromperait : l'en-tête et chaque
    résultat doivent pouvoir s'écrire, sinon rien ne l'est."""
    # Colonne B occupée : les résultats vont en C, sous un en-tête en C1.
    original = {(0, 0): "Nom", (1, 0): "Autre", (0, 1): "a", (1, 1): "pris", (0, 2): "b"}
    sheet = _protected_sheet(original, unlocked)
    doc = FakeCalcDoc(sheet, selection_ref="A2:A3")
    result = _registry().call_tool("calc_write_result_column",
                                   {"values": ["A", "B"]}, _calc_ctx(doc))
    assert not result.ok
    assert result.error == ("Résultats non écrits : la colonne C contient des cellules "
                            "verrouillées (feuille protégée).")
    assert sheet.grid == original


def test_calc_result_column_fills_the_unlocked_cells_of_a_protected_sheet():
    original = {(0, 0): "Nom", (1, 0): "Autre", (0, 1): "a", (1, 1): "pris", (0, 2): "b"}
    sheet = _protected_sheet(original, unlocked={(2, 0), (2, 1), (2, 2)})
    doc = FakeCalcDoc(sheet, selection_ref="A2:A3")
    result = _registry().call_tool("calc_write_result_column",
                                   {"values": ["A", "B"]}, _calc_ctx(doc))
    assert result.ok
    assert sheet.grid == {**original, (2, 0): "Résultat IA", (2, 1): "A", (2, 2): "B"}


def test_calc_result_column_leaves_the_locked_cells_it_does_not_write_alone():
    sheet = _protected_sheet({(0, 1): "a", (0, 2): "b", (0, 3): "c"}, unlocked={(1, 1), (1, 2)})
    doc = FakeCalcDoc(sheet, selection_ref="A2:A4")
    result = _registry().call_tool("calc_write_result_column",
                                   {"values": ["A", "B"]}, _calc_ctx(doc))
    assert result.ok
    assert sheet.grid[(1, 1)] == "A" and sheet.grid[(1, 2)] == "B"
    assert (1, 0) not in sheet.grid and (1, 3) not in sheet.grid


def test_calc_get_selection_overview():
    sheet = FakeCalcSheet(grid={(0, 0): "Nom", (0, 1): "Alice"})
    doc = FakeCalcDoc(sheet, selection_ref="A1:A2")
    result = _registry().call_tool("calc_get_selection", {}, _calc_ctx(doc))
    assert result.ok and "A1:A2" in result.content
    assert result.data["n_rows"] == 2


def test_calc_get_selection_preview_gives_each_formula_next_to_its_value():
    sheet = FakeCalcSheet(grid={(0, 0): "Total", (1, 0): "160"})
    sheet.formulas[(1, 0)] = "=SUM(B2:B5)"
    doc = FakeCalcDoc(sheet, selection_ref="A1:B1")
    result = _registry().call_tool("calc_get_selection", {}, _calc_ctx(doc))
    assert result.content.endswith("Aperçu :\nTotal | 160 (formule =SUM(B2:B5))")


def test_calc_sheet_overview_gives_the_formulas_of_the_current_row():
    sheet = FakeCalcSheet(grid={(0, 0): "Prix", (1, 0): "Log", (2, 0): "Note",
                                (0, 2): "0", (1, 2): "Err:502", (2, 2): ""})
    sheet.formulas.update({(1, 2): "=LOG10(A3)", (2, 2): '=IF(A3>9;"cher";"")'})
    doc = FakeCalcDoc(sheet, selection_ref="D3")
    result = _registry().call_tool("calc_get_sheet_overview", {}, _calc_ctx(doc))
    assert ("Current row values: 'Prix'='0', 'Log'='Err:502 (formule =LOG10(A3))', "
            """'Note'='(formule =IF(A3>9;"cher";""))'""") in result.content
