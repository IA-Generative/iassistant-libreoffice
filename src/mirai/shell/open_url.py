"""Ouverture d'une URL dans le navigateur de l'utilisateur.

Python d'abord : `webbrowser.open` rend False, sans lever, quand il ne trouve
aucun navigateur. LibreOffice ensuite, par `SystemShellExecute`. Le journal ne
reçoit jamais l'URL : LibreOffice la recopie dans le texte de ses exceptions.
"""

import logging
import webbrowser

from com.sun.star.system import SystemShellExecuteFlags

# URIS_ONLY : jamais un fichier ni un programme local. NO_SYSTEM_ERROR_MESSAGE :
# sous Windows, un échec lève au lieu d'afficher une boîte système et de rendre
# la main comme si la page était ouverte.
_FLAGS = SystemShellExecuteFlags.URIS_ONLY | SystemShellExecuteFlags.NO_SYSTEM_ERROR_MESSAGE


def open_url(ctx, url):
    """Vrai si un navigateur a pris l'URL ; faux si ni Python ni LibreOffice
    n'ont pu l'ouvrir."""
    try:
        if webbrowser.open(url):
            return True
    except Exception as exc:
        logging.info("webbrowser.open failed: %s", type(exc).__name__)
    logging.info("Python opened no browser: opening the URL through LibreOffice")
    try:
        ctx.getServiceManager().createInstanceWithContext(
            "com.sun.star.system.SystemShellExecute", ctx).execute(url, "", _FLAGS)
    except Exception as exc:
        logging.info("LibreOffice opened no browser: %s", type(exc).__name__)
        return False
    return True
