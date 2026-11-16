#!/usr/bin/env python3
"""Paquet neutre : aucune valeur d'organisation dans ce que la CI publie.

Le paquet publié est le même pour toutes les instances ; chaque Device
Management y inscrit ses valeurs à l'import. Deux commandes, appelées par
02-build-oxt.sh --neutral :

    neutral_package.py dm-config <dm-config.json> <sortie>
        Copie le modèle de configuration en remplaçant toute valeur qui porte
        une adresse réelle par le repère ${{CLE}} que le DM résout
        (portal_url -> ${{PORTAL_URL}}). Les repères déjà présents et les
        adresses locales (localhost) sont gardés.

    neutral_package.py check <dossier du paquet>
        Refuse le paquet si config.default.json nomme un DM hors de `.invalid`
        ou porte autre chose que le transport, si dm-config.json garde une
        adresse réelle, ou si le flux <update-information> de description.xml
        vise un autre hôte qu'une adresse d'exemple.

Codes de sortie : 0 ; 1 paquet refusé ; 2 usage.
"""
import json
import os
import re
import sys
import urllib.parse

_URL_RE = re.compile(r"https?://[^\s\"'<>,;]+", re.I)
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
_TRANSPORT_KEYS = {"configVersion", "enabled", "bootstrap_urls", "bootstrap_url", "config_path",
                   "_note", "_description"}
_SRC_RE = re.compile(r"<update-information\s*>(.*?)</update-information\s*>", re.S)
_HREF_RE = re.compile(r"xlink:href\s*=\s*[\"']([^\"']*)[\"']")


def _host(url):
    try:
        return (urllib.parse.urlsplit(url).hostname or "").rstrip(".").lower()
    except ValueError:
        return ""


def is_example_host(host):
    return host == "invalid" or host.endswith(".invalid")


def real_hosts(text):
    """Hôtes des adresses de `text` qui désignent un vrai service."""
    hosts = {_host(url) for url in _URL_RE.findall(str(text))}
    return sorted(h for h in hosts if h and h not in _LOCAL_HOSTS and not is_example_host(h))


def placeholder(key):
    """`portal_url` -> `${{PORTAL_URL}}`, `keycloakIssuerUrl` -> `${{KEYCLOAK_ISSUER_URL}}`."""
    return "${{" + re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key).upper() + "}}"


def neutral_dm_config(node):
    if isinstance(node, list):
        return [neutral_dm_config(item) for item in node]
    if not isinstance(node, dict):
        return node
    out = {}
    for key, value in node.items():
        if isinstance(value, str) and real_hosts(value):
            out[key] = placeholder(key)
        else:
            out[key] = neutral_dm_config(value)
    return out


def _strings(node, path=""):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _strings(value, f"{path}.{key}" if path else key)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _strings(value, f"{path}[{i}]")
    elif isinstance(node, str):
        yield path, node


def _load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def check(package_dir):
    """Liste des écarts à la neutralité (vide : le paquet est neutre)."""
    problems = []
    config = _load(os.path.join(package_dir, "config.default.json"))
    extra = sorted(set(config) - _TRANSPORT_KEYS)
    if extra:
        problems.append("config.default.json : clés hors transport : " + ", ".join(extra))
    urls = list(config.get("bootstrap_urls") or []) + [config.get("bootstrap_url") or ""]
    urls = [str(u) for u in urls if str(u or "").strip()]
    if not urls or any(not is_example_host(_host(u)) for u in urls):
        problems.append(f"config.default.json : liste de DM {urls}, adresse d'exemple en "
                        ".invalid attendue")
    dm_config = os.path.join(package_dir, "dm-config.json")
    if os.path.isfile(dm_config):
        for path, value in _strings(_load(dm_config)):
            if real_hosts(value):
                problems.append(f"dm-config.json : {path} porte une adresse réelle "
                                f"({', '.join(real_hosts(value))})")
    with open(os.path.join(package_dir, "description.xml"), encoding="utf-8") as fh:
        description = fh.read()
    for block in _SRC_RE.findall(description):
        for href in _HREF_RE.findall(block):
            if not is_example_host(_host(href)):
                problems.append(f"description.xml : flux de mise à jour vers {href}")
    return problems


def main(argv):
    if len(argv) == 3 and argv[0] == "dm-config":
        neutral = neutral_dm_config(_load(argv[1]))
        with open(argv[2], "w", encoding="utf-8") as fh:
            json.dump(neutral, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        return 0
    if len(argv) == 2 and argv[0] == "check":
        problems = check(argv[1])
        for problem in problems:
            sys.stderr.write(f"NON NEUTRE : {problem}\n")
        if not problems:
            print("✓ paquet neutre : aucune adresse d'organisation")
        return 1 if problems else 0
    sys.stderr.write(__doc__ + "\n")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
