"""Pont coquille → moteur : points d'entrée appelés par le dispatcher de la
coquille (palette, sonde du modèle).

Import paresseux depuis le dispatcher de la coquille — zéro coût au chargement
de l'extension tant qu'aucune action du moteur n'est déclenchée.
"""

from ..i18n import t as _t
from .shell_facade import MainJobShell


def test_model_capabilities(job):
    """Mesure ce que le modèle sait faire avec des outils, et le dit.

    Déclenché depuis le menu : la sonde coûte deux allers-retours, elle ne doit
    pas surgir au milieu du travail de l'utilisateur. Le verdict est mémorisé
    par couple (endpoint, modèle) et sert ensuite à choisir le chemin
    d'exécution — au lieu de le deviner à la formulation du prompt.
    """
    from . import capabilities as caps
    from .llm_client import LLMClient

    shell = MainJobShell(job)
    endpoint = str(shell.get_config("llm_base_urls", "") or "")
    model_name = str(shell.get_config("llm_default_models", "") or "")

    shell.log(f"[capabilities] sonde du modèle {model_name!r}")
    try:
        verdict = caps.probe(LLMClient(shell), model=model_name)
    except Exception as exc:
        shell.log(f"[capabilities] sonde impossible : {exc}")
        job._show_message(
            _t("entry.test_title"),
            _t("entry.test_failed", detail=exc))
        return None

    shell.log(f"[capabilities] {model_name} → accepte={verdict.accepts_tools} "
              f"appelle={verdict.calls_tool} enchaîne={verdict.chains} "
              f"({verdict.detail})")
    shell.telemetry("AssistantModelProbe", {
        "plugin.action": "assistant.probe",
        "assistant.accepts_tools": str(verdict.accepts_tools).lower(),
        "assistant.calls_tool": str(verdict.calls_tool).lower(),
        "assistant.chains": str(verdict.chains).lower(),
    })

    # Une sonde interrompue ne dit rien du modèle. Ni verdict annoncé, ni
    # verdict mémorisé : il écraserait le précédent.
    if verdict.error:
        job._show_message(
            _t("entry.test_title"),
            _t("entry.test_failed", detail=verdict.detail))
        return verdict

    caps.save_cached(shell, endpoint, model_name, verdict)
    job._show_message(
        _t("entry.test_title"),
        _t("entry.model_line", model=model_name or _t("entry.model_undefined"))
        + "\n\n" + verdict.summary() + "\n\n"
        + _t("entry.detail", detail=verdict.detail))
    return verdict


def _open_attributes(shell, model, app):
    """Ce qu'on sait de la situation à l'ouverture — sans réseau, sans document.

    Deux informations décident du chemin d'exécution et n'étaient nulle part :

    - `selection.active` : une demande libre porte sur la sélection quand elle
      existe, sur le document entier sinon ;
    - `caps.measured` / `caps.agentic` : le verdict est MESURÉ par le menu
      « Tester le modèle » et son défaut, en l'absence de mesure, est NON. Un
      poste jamais sondé ne passe donc jamais en mode agentique — mais rien ne
      permettait de distinguer « sonde jamais lancée » (un geste utilisateur le
      corrige) de « modèle incapable d'enchaîner » (il faut changer de modèle).
    """
    from . import capabilities as caps

    attributes = {"plugin.action": "assistant.open", "assistant.app": app}

    selected = ""
    if app == "writer":
        try:
            selected = model.CurrentController.getSelection().getByIndex(0).getString()
        except Exception:
            selected = ""
    attributes["selection.active"] = bool((selected or "").strip())

    verdict = None
    try:
        verdict = caps.load_cached(shell,
                                   shell.get_config("llm_base_urls", ""),
                                   shell.get_config("llm_default_models", ""))
    except Exception:
        verdict = None
    attributes["caps.measured"] = verdict is not None
    attributes["caps.agentic"] = bool(verdict and verdict.supports_agentic)
    return attributes


def open_palette(job, model):
    """Ouvre la palette universelle sur le document courant."""
    shell = MainJobShell(job)
    if hasattr(model, "Text"):
        app = "writer"
    elif hasattr(model, "Sheets"):
        app = "calc"
    else:
        shell.log(f"[palette] composant courant sans Text/Sheets : {type(model)}")
        try:
            job._show_message(_t("palette.title"), _t("entry.need_document"))
        except Exception:
            pass
        return None

    shell.telemetry("AssistantOpen", _open_attributes(shell, model, app))
    shell.log(f"[palette] ouverture demandée app={app}")

    try:
        from ..ui.palette import open_or_focus
        palette = open_or_focus(job.ctx, shell, app)
        shell.log("[palette] ouverte")
        return palette
    except Exception:
        import traceback
        shell.log("[palette] ÉCHEC d'ouverture :\n" + traceback.format_exc())
        return None


def open_eloignement(job, model):
    """Commande « Éloignement » (palette, barre d'outils)."""
    shell = MainJobShell(job)
    if not shell.feature_enabled("eloignement"):
        shell.log("[eloignement] commande ignorée : interrupteur éteint")
        return None
    try:
        from ..ui.eloignement import open_eloignement as _open
        return _open(shell, model)
    except Exception:
        import traceback
        shell.log("[eloignement] ÉCHEC :\n" + traceback.format_exc())
        return None


def _job_model(args):
    """Le document d'un événement de job (`Environment` → `Model`), ou None."""
    for item in args or ():
        if getattr(item, "Name", "") != "Environment":
            continue
        for env in getattr(item, "Value", None) or ():
            if getattr(env, "Name", "") == "Model":
                return env.Value
    return None


def eloignement_on_load(job, args):
    """Événements onLoad/onNew : barre d'outils et suivi d'un document lié.

    Pas encore de réponse du DM (démarrage) : l'écouteur d'enregistrement est
    posé quand même sur un document DÉJÀ lié, pour qu'un Ctrl+S ne parte pas
    sans être mis en file. Interrupteur éteint : seule la barre d'outils créée
    du temps où il était allumé est retirée."""
    shell = MainJobShell(job)
    model = _job_model(args)
    state = shell.feature_state("eloignement")
    if model is None or state is None and not _linked(model):
        return False
    if state is False and not shell.get_config("eloignement_toolbar", False):
        return False
    try:
        if state is None:
            from .eloignement.session import get_session
            get_session(shell).attach(model)
            return True
        from ..ui.eloignement import document_event
        return document_event(shell, model, state)
    except Exception as exc:
        shell.log(f"[eloignement] suivi du document impossible : {exc}")
        return False


def _linked(model):
    from .eloignement import link
    try:
        return link.read_link(model) is not None
    except Exception:
        return False
