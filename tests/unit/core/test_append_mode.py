"""La case « Ajouter à la suite » doit piloter TOUTES les destinations.

Elle avait été branchée sur un seul chemin — celui du preset « Modifier » —
et ce preset a ensuite été retiré des chips. La case est alors restée à
l'écran sans plus rien commander : cochée ou non, le résultat était le même.

Le remède est structurel : un helper UNIQUE traduit le choix en destination,
et chaque preset le traverse obligatoirement.
"""

from unittest.mock import MagicMock

from src.mirai.core.presets import text_sink
from src.mirai.core.sinks import WriterInsertSink, WriterReplaceSink


def _ctx():
    return MagicMock()


def test_checked_appends_between_markers():
    sink = text_sink(_ctx(), True, "\n---début---\n", "\n---fin---\n")

    assert isinstance(sink, WriterInsertSink)
    assert sink.header_marker == "\n---début---\n"
    assert sink.footer_marker == "\n---fin---\n"


def test_unchecked_replaces_the_selection():
    sink = text_sink(_ctx(), False, "\n---début---\n", "\n---fin---\n")

    assert isinstance(sink, WriterReplaceSink)


def test_extra_options_survive_in_append_mode():
    """Stop phrases et détection de question ne doivent pas être perdues."""
    sink = text_sink(_ctx(), True, "a", "b",
                     stop_phrases=["stop"], question_patterns=["?"])

    assert sink.stop_phrases == ["stop"]
    assert sink.question_patterns == ["?"]


def test_every_writer_preset_accepts_the_choice():
    """Un preset qui n'accepte pas le paramètre l'ignorerait en silence."""
    import inspect

    from src.mirai.core import presets

    for preset in presets.presets_for("writer"):
        if preset.mode != "pipeline":
            continue
        signature = inspect.signature(preset.runner)
        assert "append_mode" in signature.parameters, (
            f"{preset.id} ne reçoit pas le choix de destination")


def test_defaults_match_historic_behaviour():
    """Sans choix transmis, chaque preset garde son comportement d'origine."""
    import inspect

    from src.mirai.core import presets

    expected = {"summarize": True, "simplify": True,
                "shorten": False, "lengthen": False}
    for preset_id, appends in expected.items():
        runner = next(p for p in presets.PRESETS if p.id == preset_id).runner
        default = inspect.signature(runner).parameters["append_mode"].default
        assert default is appends, f"{preset_id} : défaut inattendu"


def test_free_prompt_with_selection_targets_the_document():
    """Un prompt libre sur une sélection modifie le document, pas la palette.

    Sans ce chemin, la demande partait en mode agentique dont le sink est la
    palette : le texte s'affichait dans la fenêtre et le document restait
    inchangé — la case « Ajouter à la suite » n'avait alors rien à piloter.
    """
    from src.mirai.core.doc_rewrite import wants_document_rewrite

    # La détection d'intention ne dépend pas de la présence d'une sélection :
    # c'est l'appelant qui choisit la cible selon qu'il y en a une ou non.
    assert wants_document_rewrite("mets ce passage au passé simple")
    assert wants_document_rewrite("reformate en alexandrins")


def test_both_targets_use_the_same_sink_helper():
    """Sélection et document passent par le MÊME traducteur de choix."""
    import inspect

    from src.mirai.ui import palette

    source = inspect.getsource(palette.AssistantPalette._run_selection_rewrite)
    assert "text_sink" in source, (
        "la réécriture de sélection doit passer par le helper commun, "
        "sinon la case redevient inopérante sur ce chemin")
