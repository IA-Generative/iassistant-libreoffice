"""Tools Writer — lectures de contexte et mutations structurelles courtes.

Invariant de conception : la prose longue générée par le LLM ne transite
JAMAIS en argument JSON — elle arrive par le sink de streaming (voir
orchestrator). Les tools mutants restent donc courts et sûrs.
"""

from ..tool_calls import ToolResult, ToolSpec

try:
    from com.sun.star.text.ControlCharacter import PARAGRAPH_BREAK
except ImportError:      # hors LibreOffice (tests)
    PARAGRAPH_BREAK = 0


def _selection_range(ctx):
    return ctx.controller.getSelection().getByIndex(0)


def get_selection(ctx, args):
    rng = _selection_range(ctx)
    text = rng.getString()
    if not text.strip():
        content = "La sélection est vide (curseur simple, aucun texte sélectionné)."
    else:
        content = f"SÉLECTION ({len(text)} caractères) :\n{text}"
    return ToolResult(
        call_id="", ok=True, content=content,
        data={"text": text, "char_count": len(text), "is_empty": not text.strip()},
    )


def get_document_map(ctx, args):
    max_chars = int(args.get("max_chars", 6000))
    lines = []
    total = 0
    index = 0
    enumeration = ctx.model.Text.createEnumeration()
    truncated = False
    while enumeration.hasMoreElements():
        para = enumeration.nextElement()
        try:
            if not para.supportsService("com.sun.star.text.Paragraph"):
                continue
        except Exception:
            continue
        index += 1
        content = para.getString()
        # Le style est indiqué au modèle : sans lui, il fusionne un titre avec
        # le corps du texte sans savoir qu'il détruit la mise en forme.
        style = ""
        try:
            name = str(para.getPropertyValue("ParaStyleName") or "")
            if name and name not in ("Standard", "Default Paragraph Style",
                                     "Corps de texte", "Text Body"):
                style = f" <{name}>"
        except Exception:
            style = ""
        line = (f"[P{index}]{style} {content}" if content.strip()
                else f"[P{index}]{style} (vide)")
        total += len(line) + 1
        if total > max_chars:
            truncated = True
            break
        lines.append(line)
    body = "\n".join(lines)
    if truncated:
        body += f"\n[... document tronqué à {max_chars} caractères ...]"
    else:
        # Dire explicitement où s'arrête le document : sans ce repère, une
        # demande portant sur « tout le document » ne traitait que les premiers
        # paragraphes, le modèle ignorant combien il y en avait.
        body += (f"\n[FIN DU DOCUMENT — {index} paragraphes au total, "
                 f"de [P1] à [P{index}]]")
    # Rappel d'action DANS le résultat de l'outil. Une consigne du prompt
    # système se dilue ; celle-ci arrive juste avant que le modèle ne décide de
    # son prochain coup. Sans elle, il lisait le document (iterations=2) puis
    # répondait du texte, en laissant le document intact.
    body += ("\n\nÉTAPE SUIVANTE OBLIGATOIRE : si la demande implique de "
             "modifier le document, appelle MAINTENANT writer_replace_paragraphs "
             "avec le texte réécrit. Ne réponds pas le texte modifié en clair — "
             "il ne serait PAS appliqué au document.")
    return ToolResult(call_id="", ok=True, content=body or "(document vide)",
                      data={"paragraph_count": index, "truncated": truncated})


def _paragraphs(ctx):
    """Liste les objets paragraphe du document, dans l'ordre."""
    items = []
    enumeration = ctx.model.Text.createEnumeration()
    while enumeration.hasMoreElements():
        para = enumeration.nextElement()
        try:
            if para.supportsService("com.sun.star.text.Paragraph"):
                items.append(para)
        except Exception:
            continue
    return items


def paragraph_style(para):
    """Nom du style d'un paragraphe, ou chaîne vide. Jamais d'exception."""
    try:
        return str(para.getPropertyValue("ParaStyleName") or "")
    except Exception:
        return ""


def replace_paragraphs(ctx, args):
    """Remplace une plage de paragraphes [Pn]…[Pm] par un nouveau texte.

    C'est le pendant écriture de `writer_get_document_map` : sans lui, le
    modèle sait lire le document numéroté mais n'a aucun moyen d'agir dessus
    hors sélection — il répond alors du texte au lieu de modifier le document.

    Le texte de remplacement peut contenir des sauts de ligne : chacun crée un
    paragraphe. C'est ce qui permet « restructure ce document en deux
    paragraphes ».
    """
    start = int(args["start"])
    end = int(args.get("end", start))
    text = str(args["text"])
    if start < 1 or end < start:
        return ToolResult(call_id="", ok=False, content="",
                          error=f"plage de paragraphes invalide : {start}..{end}")

    paragraphs = _paragraphs(ctx)
    if start > len(paragraphs):
        return ToolResult(
            call_id="", ok=False, content="",
            error=f"le document ne contient que {len(paragraphs)} paragraphe(s)")
    end = min(end, len(paragraphs))

    ctx.undo_begin("Réécriture de paragraphes")
    targets = paragraphs[start - 1:end]
    new_texts = text.split("\n")

    # Remplacer PARAGRAPHE PAR PARAGRAPHE, jamais une plage d'un seul geste.
    # `setString` sur une étendue couvrant plusieurs paragraphes applique le
    # style du PREMIER à tout le bloc : un document dont [P1] est un titre se
    # retrouvait intégralement en style titre. Écrire dans chaque paragraphe
    # séparément préserve le style de chacun.
    # strict=False assumé : les longueurs diffèrent dès qu'on change le nombre
    # de paragraphes, et le surplus est traité juste après.
    for para, new_text in zip(targets, new_texts, strict=False):
        para.setString(new_text)

    body = ctx.model.Text
    if len(new_texts) < len(targets):
        # Moins de paragraphes qu'avant : vider puis supprimer le surplus.
        for para in targets[len(new_texts):]:
            try:
                body.removeTextContent(para)
            except Exception:
                para.setString("")
    elif len(new_texts) > len(targets):
        # Plus de paragraphes qu'avant : les ajouter après le dernier, qui
        # sert de modèle de style.
        cursor = body.createTextCursorByRange(targets[-1].getEnd())
        for extra in new_texts[len(targets):]:
            body.insertControlCharacter(cursor, PARAGRAPH_BREAK, False)
            body.insertString(cursor, extra, False)

    return ToolResult(
        call_id="", ok=True,
        content=(f"{len(targets)} paragraphe(s) remplacé(s) par "
                 f"{len(new_texts)} paragraphe(s) ; styles préservés."),
        data={"replaced": len(targets), "written": len(new_texts),
              "start": start, "end": end})


def replace_selection(ctx, args):
    text = args["text"]
    rng = _selection_range(ctx)
    previous_length = len(rng.getString())
    rng.setString(text)
    try:
        ctx.controller.select(rng)
    except Exception:
        pass
    return ToolResult(
        call_id="", ok=True,
        content=f"Sélection remplacée ({previous_length} → {len(text)} caractères).",
    )


def insert_text(ctx, args):
    content = args["text"]
    position = args.get("position", "after_selection")
    if position == "end_of_document":
        text_obj = ctx.model.Text
        cursor = text_obj.createTextCursor()
        cursor.gotoEnd(False)
    else:
        rng = _selection_range(ctx)
        text_obj = rng.getText()
        cursor = text_obj.createTextCursorByRange(rng)
        cursor.collapseToEnd()
    text_obj.insertString(cursor, content, False)
    return ToolResult(call_id="", ok=True,
                      content=f"{len(content)} caractères insérés ({position}).")


def find_replace(ctx, args):
    pairs = args["pairs"]
    replace_all = bool(args.get("replace_all", False))
    doc = ctx.model
    applied = 0
    not_found = []
    for pair in pairs:
        find = str(pair.get("find", ""))
        replacement = str(pair.get("replace", ""))
        if not find:
            continue
        descriptor = doc.createSearchDescriptor()
        descriptor.SearchString = find
        try:
            descriptor.SearchCaseSensitive = True
        except Exception:
            pass
        found = doc.findFirst(descriptor)
        if found is None:
            not_found.append(find[:80])
            continue
        while found is not None:
            found.setString(replacement)
            applied += 1
            if not replace_all:
                break
            found = doc.findNext(found.getEnd(), descriptor)
    summary = f"{applied} remplacement(s) effectué(s)."
    if not_found:
        summary += " Introuvable (texte exact requis) : " + " | ".join(not_found)
    return ToolResult(call_id="", ok=True, content=summary,
                      data={"applied": applied, "not_found": not_found})


def register(registry):
    registry.register(ToolSpec(
        name="writer_get_selection",
        description="Lit le texte actuellement sélectionné dans le document Writer.",
        parameters={"type": "object", "properties": {}},
        handler=get_selection, apps=("writer",),
    ))
    registry.register(ToolSpec(
        name="writer_get_document_map",
        description=("Lit le document entier sous forme de paragraphes numérotés "
                     "[P1], [P2]… Utile pour repérer où intervenir."),
        parameters={"type": "object", "properties": {
            "max_chars": {"type": "integer", "default": 6000, "minimum": 500, "maximum": 20000},
        }},
        handler=get_document_map, apps=("writer",),
    ))
    registry.register(ToolSpec(
        name="writer_replace_paragraphs",
        description=(
            "Remplace les paragraphes [Pstart] à [Pend] (numéros donnés par "
            "writer_get_document_map) par un nouveau texte. C'est L'OUTIL à "
            "utiliser pour restructurer, réorganiser ou réécrire tout ou partie "
            "du document quand rien n'est sélectionné. Les sauts de ligne du "
            "texte créent de nouveaux paragraphes. "
            "POUR TOUT LE DOCUMENT : start=1 et end = le DERNIER numéro indiqué "
            "par la carte (elle se termine par « FIN DU DOCUMENT — N paragraphes »). "
            "ATTENTION AUX STYLES : la carte signale les styles entre chevrons, "
            "par exemple « [P1] <Titre> ». Le style de chaque paragraphe est "
            "conservé, donc n'écrase JAMAIS un titre avec du corps de texte — "
            "laisse-le hors de la plage, ou garde-le comme premier paragraphe "
            "de ton remplacement."),
        parameters={"type": "object", "properties": {
            "start": {"type": "integer", "minimum": 1,
                      "description": "Numéro du premier paragraphe à remplacer (1 = [P1])."},
            "end": {"type": "integer", "minimum": 1,
                    "description": "Numéro du dernier paragraphe inclus. Égal à start "
                                   "pour n'en remplacer qu'un."},
            "text": {"type": "string",
                     "description": "Texte de remplacement ; « \\n » sépare les paragraphes."},
        }, "required": ["start", "text"]},
        handler=replace_paragraphs, apps=("writer",),
    ))
    registry.register(ToolSpec(
        name="writer_replace_selection",
        description=("Remplace le texte sélectionné par un texte court. Pour une "
                     "réécriture longue, réponds plutôt en texte final (elle sera "
                     "insérée automatiquement)."),
        parameters={"type": "object", "properties": {
            "text": {"type": "string"},
        }, "required": ["text"]},
        handler=replace_selection, apps=("writer",), mutates=True,
    ))
    registry.register(ToolSpec(
        name="writer_insert_text",
        description="Insère un texte après la sélection ou en fin de document.",
        parameters={"type": "object", "properties": {
            "text": {"type": "string"},
            "position": {"type": "string", "enum": ["after_selection", "end_of_document"],
                         "default": "after_selection"},
        }, "required": ["text"]},
        handler=insert_text, apps=("writer",), mutates=True,
    ))
    registry.register(ToolSpec(
        name="writer_find_replace",
        description=("Remplace des passages exacts du document. Chaque paire "
                     "{find, replace} : 'find' doit reproduire exactement le texte "
                     "du document (copie depuis writer_get_document_map)."),
        parameters={"type": "object", "properties": {
            "pairs": {"type": "array", "items": {"type": "object", "properties": {
                "find": {"type": "string"},
                "replace": {"type": "string"},
            }, "required": ["find", "replace"]}},
            "replace_all": {"type": "boolean", "default": False},
        }, "required": ["pairs"]},
        handler=find_replace, apps=("writer",), mutates=True,
    ))
