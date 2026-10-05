#!/usr/bin/env python3
"""Injecte le bloc <update-information> dans description.xml au moment du build.

Branche le mécanisme NATIF de mise à jour d'extensions de LibreOffice :
avec ce bloc, le bouton « Vérifier les mises à jour » du Gestionnaire des
extensions (et la vérification périodique de LO, si activée) interroge le feed
update.xml servi par le DM, télécharge et installe l'OXT entièrement in-process
— aucun cmd.exe, immunisé WinError 5 (AppLocker / Defender ASR).

L'URL du feed est dérivée du profil bootstrap embarqué (config.default.json du
staging) : chaque bootstrap_url donne un <src> — LibreOffice les essaie dans
l'ordre (failover natif, même sémantique que le multi-bootstrap du plugin).
Convention de chemin servie par le DM :

    <bootstrap>/catalog/mirai-libreoffice/update.xml

Overrides :
  - MIRAI_UPDATE_FEED_URL : URL complète du feed (gagne sur le profil) ;
  - profil offline (enabled:false) ou sans bootstrap_urls : aucun bloc injecté
    (le bouton natif répond simplement « aucune mise à jour »).

Usage : inject_update_feed.py <description.xml> <config.default.json>
Idempotent : si un bloc <update-information> est déjà présent, ne touche à rien.
"""
import json
import os
import sys
import xml.etree.ElementTree as ET
from xml.sax.saxutils import quoteattr

FEED_PATH = "/catalog/mirai-libreoffice/update.xml"


def feed_urls(config_path):
    override = os.environ.get("MIRAI_UPDATE_FEED_URL", "").strip()
    if override:
        return [override]
    try:
        with open(config_path, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except Exception:
        return []
    if cfg.get("enabled") is False:
        return []
    bases = cfg.get("bootstrap_urls")
    if not bases and cfg.get("bootstrap_url"):
        bases = [cfg["bootstrap_url"]]
    return [
        base.rstrip("/") + FEED_PATH
        for base in (bases or [])
        if isinstance(base, str) and base.strip()
    ]


def inject(description_path, config_path):
    """Retourne un message de statut ; lève en cas de description.xml invalide."""
    urls = feed_urls(config_path)
    if not urls:
        return "update-information: no feed URL (offline profile) — skipped"

    with open(description_path, encoding="utf-8") as fh:
        xml_text = fh.read()
    if "<update-information>" in xml_text:
        return "update-information: already present — left as-is"
    if "</description>" not in xml_text:
        raise ValueError("description.xml: missing </description>")

    block = "  <update-information>\n" + "".join(
        f"    <src xlink:href={quoteattr(url)}/>\n" for url in urls
    ) + "  </update-information>\n"
    xml_text = xml_text.replace("</description>", block + "</description>")

    # Sanity : le résultat doit rester un XML bien formé (sinon LO rejette l'OXT).
    ET.fromstring(xml_text.encode("utf-8"))

    with open(description_path, "w", encoding="utf-8") as fh:
        fh.write(xml_text)
    return f"update-information baked ({len(urls)} feed URL(s))"


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.stderr.write(__doc__ + "\n")
        sys.exit(2)
    print(inject(sys.argv[1], sys.argv[2]))
