"""Tools Éloignement du moteur.

Ils suivent le contrat des autres tools (ToolSpec, ToolResult, télémétrie par
le registre : nom, statut, durée, jamais d'argument ni de contenu). Ils vivent
dans un registre À PART : ni `register_all` ni le mode agentique ne les voient.
Les dossiers contiennent des données personnelles, et rien ne doit partir vers
un modèle de langage sans geste de l'agent ; ce sont les écrans qui les
appellent.

Chaque handler tourne dans un thread de travail : réseau sur place, UNO par
`ctx.on_main`.
"""

import dataclasses

from ..registry import ToolRegistry
from ..tool_calls import ToolResult, ToolSpec
from . import api, fill
from . import link as link_mod
from . import options as options_mod
from .session import get_session, safe_filename, unique_path


def _fail(message):
    return ToolResult(call_id="", ok=False, content="", error=message)


def _guard(handler):
    """Les refus d'Éloignement deviennent des résultats lisibles."""
    def wrapped(ctx, args):
        try:
            return handler(ctx, args)
        except api.EloignementError as exc:
            return _fail(exc.message)
        except ValueError as exc:
            return _fail(str(exc))
    return wrapped


def _session(ctx):
    session = get_session(ctx.shell)
    session.require()
    return session


def lister_modeles(ctx, args):
    session = _session(ctx)
    modeles = session.client.modeles(args.get("q", ""))
    return ToolResult("", True, f"{len(modeles)} modèle(s)", data={"modeles": modeles})


def rechercher_dossier(ctx, args):
    session = _session(ctx)
    dossiers = session.client.rechercher(args["q"])
    return ToolResult("", True, f"{len(dossiers)} dossier(s)", data={"dossiers": dossiers})


def lire_dossier(ctx, args):
    session = _session(ctx)
    dossier = session.client.dossier(args["dossier"])
    return ToolResult("", True, "en-tête lu", data={"dossier": dossier})


def generer_acte(ctx, args):
    """Éloignement remplit le modèle avec le dossier ; l'acte s'ouvre lié."""
    session = _session(ctx)
    dossier_id = api.safe_id(args["dossier"])
    options = args.get("options") or None
    if options is not None and not options_mod.valid_body(options):
        return _fail("Options de génération illisibles.")
    acte = session.client.generer(dossier_id, args["modele"], options)
    document = api.safe_id(acte["id"])
    data, version = session.client.contenu(dossier_id, document)
    path = unique_path(session.workdir, safe_filename(acte["nom"] or f"acte-{document}"))
    with open(path, "wb") as fh:
        fh.write(data)
    labels = {v["nom"]: v["libelle"] for v in acte["balises_vides"]}
    modele_nom = str(args.get("modele_nom") or acte["modele"].get("nom") or "")
    link = link_mod.Link(dossier=dossier_id, modele=str(args["modele"]),
                         document=document, version=version or acte["version"])

    def open_and_scan():
        model = session.open_linked(path, link, header=args.get("_header") or {},
                                    modele_nom=modele_nom)
        names = fill.placeholder_names(fill.find_placeholders(model))
        state = session.attach(model)
        state.empty = list(dict.fromkeys(list(labels) + names))
        state.labels = labels
        if session.ui is not None:
            session.ui.refresh(model)
        return state.empty

    empty = ctx.on_main(open_and_scan, timeout=60)
    return ToolResult("", True, f"acte ouvert, {len(empty)} balise(s) à compléter",
                      data={"chemin": path, "vides": empty, "libelles": labels})


def remplir_depuis_dossier(ctx, args):
    """Remplit les balises du document ouvert avec les valeurs du dossier."""
    session = _session(ctx)
    dossier_id = api.safe_id(args["dossier"])
    if ctx.on_main(lambda: fill.has_template_logic(ctx.model)):
        return _fail("Ce document contient des blocs conditionnels de modèle : seul Éloignement "
                     "sait les évaluer. Utilisez « Nouvel acte depuis Éloignement ».")
    names = ctx.on_main(lambda: fill.placeholder_names(fill.find_placeholders(ctx.model)))
    if not names:
        return _fail("Ce document ne contient aucune balise à remplir.")
    valeurs, libelles = session.client.balises(dossier_id)
    valeurs = {name: valeurs.get(name) for name in names}

    def write():
        filled, empty = fill.fill(ctx.model, valeurs)
        link = link_mod.read_link(ctx.model) or link_mod.Link(dossier=dossier_id)
        link.dossier = dossier_id
        link_mod.write_link(ctx.model, link)
        state = session.attach(ctx.model)
        state.key = session.key_for(ctx.model, link)
        session.known.add(dossier_id, state.key)
        state.empty, state.labels = empty, dict(libelles)
        state.header = args.get("_header") or state.header
        state.status, state.tone = f"Lié au dossier n° {dossier_id}", "success"
        return filled, empty

    filled, empty = ctx.on_main(write)
    return ToolResult("", True, f"{len(filled)} balise(s) remplie(s), {len(empty)} vide(s)",
                      data={"remplies": filled, "vides": empty, "libelles": libelles})


def lire_ecarts(ctx, _args):
    """Écarts entre les passages remplis du document et le dossier."""
    session = _session(ctx)
    link = ctx.on_main(lambda: link_mod.read_link(ctx.model))
    if link is None:
        return _fail("Ce document n'est lié à aucun dossier.")
    marks = ctx.on_main(lambda: fill.read_marks(ctx.model))
    if not marks:
        return _fail("Aucun passage rempli depuis le dossier n'a été retrouvé dans ce document.")
    balises = list(dict.fromkeys(mark.balise for mark in marks))
    valeurs, libelles = session.client.balises(link.dossier, link.modele)
    valeurs = {name: valeurs[name] for name in balises if name in valeurs}
    changes = fill.refresh_plan(marks, valeurs)
    return ToolResult("", True, f"{len(changes)} écart(s)",
                      data={"ecarts": [dataclasses.asdict(c) for c in changes],
                            "libelles": libelles})


def appliquer_ecarts(ctx, args):
    changes = [fill.Change(**{k: c.get(k) for k in
                              ("balise", "bookmark", "current", "new", "modified_by_hand")})
               for c in args.get("ecarts") or [] if isinstance(c, dict)]
    written = ctx.on_main(lambda: fill.apply_changes(ctx.model, changes))
    return ToolResult("", True, f"{written} passage(s) actualisé(s)", data={"ecrits": written})


def envoyer(ctx, _args):
    """Relance la file d'envoi (Ctrl+S l'alimente)."""
    session = _session(ctx)
    session.kick()
    waiting = len(session.outbox.entries())
    return ToolResult("", True, f"{waiting} envoi(s) en file", data={"en_file": waiting})


_STRING = {"type": "string"}

SPECS = [
    ToolSpec("eloignement_lister_modeles",
             "Liste les modèles d'actes de la préfecture de l'agent.",
             {"type": "object", "properties": {"q": _STRING}}, _guard(lister_modeles),
             apps=("writer",)),
    ToolSpec("eloignement_rechercher_dossier",
             "Recherche un dossier par prénom, nom et date de naissance, numéro étranger "
             "ou numéro de dossier.",
             {"type": "object", "properties": {"q": _STRING}, "required": ["q"]},
             _guard(rechercher_dossier), apps=("writer",)),
    ToolSpec("eloignement_lire_dossier", "Lit l'en-tête d'un dossier.",
             {"type": "object", "properties": {"dossier": _STRING}, "required": ["dossier"]},
             _guard(lire_dossier), apps=("writer",)),
    ToolSpec("eloignement_generer_acte",
             "Fait générer par Éloignement l'acte d'un modèle pour un dossier, et l'ouvre.",
             {"type": "object", "properties": {"dossier": _STRING, "modele": _STRING,
                                               "modele_nom": _STRING,
                                               "options": {"type": "object"}},
              "required": ["dossier", "modele"]},
             _guard(generer_acte), apps=("writer",)),
    ToolSpec("eloignement_remplir_depuis_dossier",
             "Remplit les balises du document ouvert avec les valeurs d'un dossier.",
             {"type": "object", "properties": {"dossier": _STRING}, "required": ["dossier"]},
             _guard(remplir_depuis_dossier), apps=("writer",)),
    ToolSpec("eloignement_lire_ecarts",
             "Compare les passages remplis du document aux valeurs actuelles du dossier.",
             {"type": "object", "properties": {}}, _guard(lire_ecarts), apps=("writer",)),
    ToolSpec("eloignement_appliquer_ecarts",
             "Écrit les écarts acceptés par l'agent.",
             {"type": "object", "properties": {"ecarts": {"type": "array"}},
              "required": ["ecarts"]},
             _guard(appliquer_ecarts), apps=("writer",), mutates=False),
    ToolSpec("eloignement_envoyer", "Relance l'envoi des actes en attente.",
             {"type": "object", "properties": {}}, _guard(envoyer), apps=("writer",)),
]


def registry():
    """Registre réservé aux écrans Éloignement (jamais proposé au modèle)."""
    reg = ToolRegistry()
    for spec in SPECS:
        reg.register(spec)
    return reg
