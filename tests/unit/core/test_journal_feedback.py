"""Onglet « Actions » : dire à l'utilisateur quand RIEN n'a été exécuté.

Le cas qui motive ces tests : « sauvegarde le document ». Aucun outil de
sauvegarde n'existe dans le registre, le modèle répond donc par du texte, et
l'onglet Actions restait vide. Trois situations très différentes devenaient
indiscernables pour l'utilisateur :

  1. l'action a bien eu lieu ;
  2. elle a été tentée et a échoué ;
  3. la capacité n'existe pas.

Le journal doit lever ce doute, et annoncer ce qui est faisable ici.
"""

from tests.stubs.uno_stubs import install

install()

from src.mirai.core.registry import ToolRegistry
from src.mirai.core.tool_calls import ToolCall, ToolResult, ToolSpec
from src.mirai.i18n import t as _t
from src.mirai.ui.palette import TOOL_LABELS, _JournalObserver


class _FakePalette:
    """Palette réduite à ce que l'observer lui demande."""

    def __init__(self, app="writer", registry=None):
        self.app = app
        self.registry = registry if registry is not None else _registry()
        self.journal = ""

    def set_journal_text(self, text):
        self.journal = text


def _registry():
    registry = ToolRegistry()
    for name in ("writer_get_selection", "writer_replace_paragraphs"):
        registry.register(ToolSpec(
            name=name, description="d",
            parameters={"type": "object", "properties": {}},
            handler=lambda ctx, args: ToolResult(call_id="", ok=True, content=""),
            apps=("writer",),
        ))
    return registry


def _observer(palette=None):
    palette = palette or _FakePalette()
    observer = _JournalObserver(palette)
    observer.on_run_start("native")
    return observer, palette


# Le run sans action

def test_run_without_any_tool_says_so():
    observer, palette = _observer()
    observer.on_final("Je ne peux pas enregistrer le document.")
    assert "Aucune action sur le document" in palette.journal


def test_available_capabilities_are_listed():
    observer, palette = _observer()
    observer.on_final("réponse en texte")
    assert "Lecture de la sélection" in palette.journal
    assert "Réécriture des paragraphes" in palette.journal


def test_capabilities_come_from_the_registry_not_a_hardcoded_list():
    """Un registre vide ne doit pas faire promettre des capacités inexistantes."""
    palette = _FakePalette(registry=ToolRegistry())
    observer, _ = _observer(palette)
    observer.on_final("réponse")
    assert "Aucune action sur le document" in palette.journal
    assert "l'assistant sait" not in palette.journal


def test_capabilities_are_scoped_to_the_current_app():
    palette = _FakePalette(app="calc")     # aucun outil calc dans ce registre
    observer, _ = _observer(palette)
    observer.on_final("réponse")
    assert "Lecture de la sélection" not in palette.journal


# Le run qui a agi : pas de bruit

def test_run_with_a_tool_stays_silent():
    observer, palette = _observer()
    call = ToolCall(id="c1", name="writer_replace_paragraphs", arguments={})
    observer.on_tool_calls([call])
    observer.on_tool_result(call, ToolResult(call_id="c1", ok=True, content="ok"), 12)
    observer.on_final("Document réécrit.")
    assert "Aucune action" not in palette.journal


def test_failed_tool_still_counts_as_an_attempt():
    """Un outil qui échoue laisse déjà sa trace ✗ : ne pas la contredire par
    « aucune action », qui ferait croire que rien n'a été tenté."""
    observer, palette = _observer()
    call = ToolCall(id="c1", name="writer_get_selection", arguments={})
    observer.on_tool_calls([call])
    observer.on_tool_result(
        call, ToolResult(call_id="c1", ok=False, content="", error="cassé"), 5)
    observer.on_final("")
    assert "Aucune action" not in palette.journal
    assert "✗" in palette.journal


def test_flag_resets_between_runs():
    observer, palette = _observer()
    call = ToolCall(id="c1", name="writer_get_selection", arguments={})
    observer.on_tool_calls([call])
    observer.on_tool_result(call, ToolResult(call_id="c1", ok=True, content=""), 3)
    observer.on_final("fait")
    assert "Aucune action" not in palette.journal

    observer.on_run_start("native")        # second run, sans outil
    observer.on_final("juste du texte")
    assert "Aucune action sur le document" in palette.journal


# Libellés lisibles

def test_replace_paragraphs_has_a_human_label():
    assert _t(TOOL_LABELS["writer_replace_paragraphs"]) == "Réécriture des paragraphes"


def test_unknown_tool_name_is_shown_as_is():
    """Le nom vient du modèle : même s'il coïncide avec une clé du catalogue,
    il s'affiche tel quel, jamais traduit."""
    observer, palette = _observer()
    observer.on_tool_calls([ToolCall(id="c1", name="common.ok", arguments={})])
    assert "⏳ common.ok…" in palette.journal


def test_every_registered_tool_has_a_label():
    """Un outil sans libellé s'affiche sous son nom technique dans le journal.
    Le test couvre les DEUX applications : la liste des capacités est rendue à
    l'utilisateur, un nom technique qui s'y glisse est visible immédiatement."""
    from src.mirai.core.tools import register_all
    registry = register_all(ToolRegistry())
    manquants = {}
    for app in ("writer", "calc"):
        sans = [spec.name for spec in registry.list_tools(app)
                if spec.name not in TOOL_LABELS]
        if sans:
            manquants[app] = sans
    assert manquants == {}, f"outils sans libellé lisible : {manquants}"
