"""Réécriture de l'adresse du feed natif dans le description.xml installé.

LibreOffice relit le description.xml de l'extension installée à CHAQUE
vérification de mises à jour (desktop/source/deployment : getUpdateInformationURLs
→ getDescriptionInfoset → lecture de <dossier>/description.xml). Le DM ne sert
sur l'adresse nue que la « version générale » (device-management#40) ; en y
ajoutant `?version=<cible>`, le plugin obtient un feed qui ne confirme que SA
cible (directive de /config) ou sa version installée. Chaque poste ne voit
ainsi que ce qui lui est destiné : ni le bouton « Vérifier les mises à jour »
ni la vérification hebdomadaire de LibreOffice ne diffusent une version en
canary au reste du parc.

Module sans dépendance UNO : testable seul, appelé par entrypoint.py.
"""

import html
import os
import re
import stat
import tempfile
import urllib.parse
import xml.etree.ElementTree as ET

# Résultats rendus à l'appelant (journal, télémétrie).
WRITTEN = "written"
UNCHANGED = "unchanged"
ABSENT = "absent"          # pas de bloc <update-information> (profil offline)
UNWRITABLE = "unwritable"  # installation en couche partagée, droits insuffisants
ERROR = "error"

_BLOCK_RE = re.compile(r"<update-information\s*>.*?</update-information\s*>", re.S)
_HREF_RE = re.compile(r'(xlink:href\s*=\s*)(["\'])(.*?)\2', re.S)


def with_version(url, version):
    """`url` avec son paramètre `version` remplacé (ou ajouté) ; le reste de
    l'URL (chemin, autres paramètres, override MIRAI_UPDATE_FEED_URL) intact."""
    parts = urllib.parse.urlsplit(url)
    query = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
             if k != "version"]
    query.append(("version", version))
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))


def rewrite_feed_version(xml_text, version):
    """Texte du description.xml avec `?version=<version>` sur chaque <src> du
    bloc <update-information>, ou None si le bloc est absent.

    Réécrit le texte plutôt que l'arbre : ElementTree renommerait les préfixes
    de namespace (ns0:) et le prologue, et le build relit <version value=…> par
    motif. Le résultat est validé comme XML bien formé. Hypothèse : le bloc est
    celui qu'écrit scripts/inject_update_feed.py (un seul, sans préfixe).
    """
    match = _BLOCK_RE.search(xml_text)
    if not match:
        return None

    def _href(m):
        url = html.unescape(m.group(3))
        escaped = html.escape(with_version(url, version), quote=True)
        return f"{m.group(1)}{m.group(2)}{escaped}{m.group(2)}"

    block = _HREF_RE.sub(_href, match.group(0))
    new_text = xml_text[:match.start()] + block + xml_text[match.end():]
    ET.fromstring(new_text.encode("utf-8"))
    return new_text


def feed_urls(xml_text):
    """Adresses <src> du bloc <update-information>, dans l'ordre (failover)."""
    match = _BLOCK_RE.search(xml_text or "")
    if not match:
        return []
    return [html.unescape(m.group(3)) for m in _HREF_RE.finditer(match.group(0))]


def rewrite_description_file(path, version):
    """Réécrit `path` si nécessaire ; renvoie (résultat, détail).

    Idempotent (aucune écriture si rien ne change : l'appel suit chaque lecture
    de /config) et atomique (fichier temporaire du même dossier puis
    os.replace) : LibreOffice ne lit jamais un fichier à moitié écrit.
    """
    try:
        # newline="" : fins de ligne conservées telles quelles (CRLF compris).
        with open(path, encoding="utf-8", newline="") as fh:
            current = fh.read()
    except (OSError, UnicodeDecodeError) as exc:
        return ERROR, f"lecture: {exc}"
    try:
        updated = rewrite_feed_version(current, version)
    except (ET.ParseError, ValueError) as exc:
        return ERROR, f"xml: {exc}"
    if updated is None:
        return ABSENT, ""
    if updated == current:
        return UNCHANGED, ""
    folder = os.path.dirname(path) or "."
    if not os.access(folder, os.W_OK) or not os.access(path, os.W_OK):
        return UNWRITABLE, folder
    tmp = None
    try:
        mode = stat.S_IMODE(os.stat(path).st_mode)
        fd, tmp = tempfile.mkstemp(prefix=".description.", suffix=".tmp", dir=folder)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(updated)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)            # mkstemp crée en 0600 : garder les droits d'origine
        os.replace(tmp, path)
        tmp = None
        return WRITTEN, ""
    except PermissionError as exc:
        return UNWRITABLE, str(exc)
    except OSError as exc:
        return ERROR, f"écriture: {exc}"
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
