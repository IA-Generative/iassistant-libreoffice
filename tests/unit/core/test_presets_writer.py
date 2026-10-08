"""Golden iso-fonctionnels Writer : chaque fonction historique rejouée via le
moteur (vrai LLMClient + SSE scripté + faux document) — marqueurs, stop
phrases, retry sur question, undo unique. Assertions portées des
comportements de menu_actions/writer.py."""

from src.mirai.core import presets
from src.mirai.core.context import ToolContext
from tests.stubs.fake_docs import FakeWriterDoc
from tests.stubs.fake_shell import FakeShell, FakeSSEResponse, text_chunks


def _ctx(doc, shell):
    return ToolContext(doc, doc.controller, "writer", shell)


def test_extend_inserts_between_legacy_markers():
    doc = FakeWriterDoc(selection_text="Il était une fois")
    shell = FakeShell(responses=[FakeSSEResponse(text_chunks(" un roi", " généreux."))])
    message = presets.run_extend(_ctx(doc, shell), shell, "", None)
    assert "---début-du-texte-généré---" in doc.inserted
    assert " un roi généreux." in doc.inserted
    assert doc.inserted.endswith("\n---fin-du-texte-généré---\n")
    assert doc.undo.entered == ["Générer la suite"]
    assert doc.undo.left == 1
    assert "générée" in message.lower()
    # span legacy avec via=palette
    spans = dict(shell.telemetry_events)
    assert spans["ExtendSelection"]["via"] == "palette"


def test_extend_no_selection_no_paragraph_returns_hint():
    doc = FakeWriterDoc(selection_text="", current_paragraph="")
    shell = FakeShell()
    message = presets.run_extend(_ctx(doc, shell), shell, "", None)
    assert "curseur" in message
    assert doc.inserted == ""


def test_extend_targets_current_paragraph_without_selection():
    # Action directe : pas de sélection → le paragraphe sous le curseur est
    # identifié, sélectionné et utilisé comme cible.
    doc = FakeWriterDoc(selection_text="",
                        current_paragraph="Le paragraphe en cours de rédaction")
    shell = FakeShell(responses=[FakeSSEResponse(text_chunks(" continue ici."))])
    message = presets.run_extend(_ctx(doc, shell), shell, "", None)
    assert " continue ici." in doc.inserted
    assert "générée" in message.lower()
    # le prompt envoyé au LLM est bien le paragraphe
    prompt = shell.requests[0]["messages"][-1]["content"]
    assert prompt == "Le paragraphe en cours de rédaction"


def test_summarize_targets_current_paragraph_without_selection():
    doc = FakeWriterDoc(selection_text="",
                        current_paragraph="Un paragraphe administratif dense.")
    shell = FakeShell(responses=[FakeSSEResponse(text_chunks("Résumé bref."))])
    presets.run_summarize(_ctx(doc, shell), shell, "", None)
    assert "Résumé bref." in doc.inserted
    prompt = shell.requests[0]["messages"][-1]["content"]
    assert "Un paragraphe administratif dense." in prompt


def test_shorten_targets_current_paragraph_without_selection():
    doc = FakeWriterDoc(selection_text="",
                        current_paragraph=" ".join(f"mot{i}" for i in range(10)))
    shell = FakeShell(responses=[FakeSSEResponse(text_chunks("court."))])
    presets.run_shorten(_ctx(doc, shell), shell, "", None)
    assert doc.replaced == "court."   # le paragraphe est remplacé


def test_extend_question_triggers_single_retry():
    doc = FakeWriterDoc(selection_text="Texte de départ")
    shell = FakeShell(responses=[
        FakeSSEResponse(text_chunks("Comment puis-je vous aider ?")),
        FakeSSEResponse(text_chunks("la suite réelle du texte.")),
    ])
    presets.run_extend(_ctx(doc, shell), shell, "", None)
    assert "Comment puis-je" not in doc.inserted
    assert "la suite réelle du texte." in doc.inserted
    # le retry porte la directive renforcée
    retry_system = shell.requests[1]["messages"][0]["content"]
    assert "INTERDIT" in retry_system


def test_extend_double_question_inserts_fallback():
    doc = FakeWriterDoc(selection_text="Texte")
    shell = FakeShell(responses=[
        FakeSSEResponse(text_chunks("Voulez-vous que je continue ?")),
        FakeSSEResponse(text_chunks("Souhaitez-vous que je poursuive ?")),
    ])
    presets.run_extend(_ctx(doc, shell), shell, "", None)
    assert "[Le modèle n'a pas pu continuer le texte." in doc.inserted


def test_summarize_markers_and_stop_phrase():
    doc = FakeWriterDoc(selection_text="Un long texte administratif.")
    shell = FakeShell(responses=[FakeSSEResponse(
        text_chunks("Résumé concis.", " [END] ceci ne doit pas apparaître"))])
    presets.run_summarize(_ctx(doc, shell), shell, "", None)
    assert "---début-du-résumé---" in doc.inserted
    assert "Résumé concis." in doc.inserted
    assert "ne doit pas apparaître" not in doc.inserted
    assert "---fin-du-résumé---" in doc.inserted
    assert doc.undo.entered == ["Résumer"]


def test_summarize_prompt_matches_legacy_template():
    doc = FakeWriterDoc(selection_text="Contenu.")
    shell = FakeShell(responses=[FakeSSEResponse(text_chunks("R."))])
    presets.run_summarize(_ctx(doc, shell), shell, "", None)
    user_prompt = shell.requests[0]["messages"][-1]["content"]
    assert user_prompt.startswith("TEXTE À RÉSUMER :")
    assert "RÉSUMÉ :" in user_prompt
    system = shell.requests[0]["messages"][0]["content"]
    assert system.startswith("/no_thinking")           # défaut hérité
    assert "résumeur professionnel" in system


def test_simplify_question_shows_legacy_message():
    doc = FakeWriterDoc(selection_text="Texte complexe.")
    shell = FakeShell(responses=[FakeSSEResponse(
        text_chunks("Voulez-vous une version simplifiée ?"))])
    presets.run_simplify(_ctx(doc, shell), shell, "", None)
    assert "[Le modèle a posé une question. Veuillez réessayer.]" in doc.inserted
    assert "Voulez-vous" not in doc.inserted.replace(
        "[Le modèle a posé une question. Veuillez réessayer.]", "")


def test_simplify_normal_flow():
    doc = FakeWriterDoc(selection_text="Texte alambiqué.")
    shell = FakeShell(responses=[FakeSSEResponse(text_chunks("Texte simple."))])
    presets.run_simplify(_ctx(doc, shell), shell, "", None)
    assert "---reformulation-du-texte---" in doc.inserted
    assert "Texte simple." in doc.inserted
    assert "---fin-de-reformulation---" in doc.inserted


def test_shorten_replaces_selection_and_reselects():
    words = " ".join(f"mot{i}" for i in range(20))
    doc = FakeWriterDoc(selection_text=words)
    shell = FakeShell(responses=[FakeSSEResponse(text_chunks("version courte."))])
    message = presets.run_shorten(_ctx(doc, shell), shell, "", None)
    assert doc.replaced == "version courte."
    assert doc.selected, "la sélection doit être restaurée pour itérer"
    assert doc.undo.entered == ["Raccourcir"]
    assert "13" in message                     # round(20 × 0.65) = 13
    prompt = shell.requests[0]["messages"][-1]["content"]
    assert "environ 13 mots" in prompt


def test_lengthen_ratio():
    words = " ".join(f"mot{i}" for i in range(10))
    doc = FakeWriterDoc(selection_text=words)
    shell = FakeShell(responses=[FakeSSEResponse(text_chunks("version longue."))])
    presets.run_lengthen(_ctx(doc, shell), shell, "", None)
    prompt = shell.requests[0]["messages"][-1]["content"]
    assert "environ 14 mots" in prompt         # round(10 × 1.4) = 14


def test_http_error_returns_user_message_writer():
    import io
    import urllib.error
    doc = FakeWriterDoc(selection_text="Texte.")
    error = urllib.error.HTTPError("http://x", 429, "quota", {},
                                   io.BytesIO(b"{}"))
    shell = FakeShell(responses=[error])
    message = presets.run_summarize(_ctx(doc, shell), shell, "", None)
    assert "Quota" in message
    assert doc.undo.left == 1                  # undo refermé malgré l'erreur
