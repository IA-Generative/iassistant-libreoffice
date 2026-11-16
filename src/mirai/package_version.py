"""Les deux écritures d'un numéro de version : celle du Device Management et
celle de LibreOffice.

Le Device Management, le nom du fichier et les notes de version portent
`X.Y.Z`, `X.Y.Z-rc.N` (candidate) ou `X.Y.Z-dev.N` (essai). LibreOffice, lui,
compare les versions d'extension élément par élément, entre les points : zéros
de tête retirés, le plus long l'emporte, puis l'ordre des caractères
(`dp_misc::compareVersions`, desktop/source/deployment/misc/dp_version.cxx).
Un suffixe n'y est pas refusé, mais mal rangé : `0.4.0-rc.12` y est PLUS GRAND
que `0.4.0`, et que `0.4.1`. Un poste en version candidate ne se verrait jamais
proposer la version finale.

`description.xml` reçoit donc, pour une pré-version, une écriture numérique qui
se range comme le veut l'ordre des versions :

    X.Y.Z            ->  X.Y.Z
    X.Y.Z-dev.N      ->  P.1.N
    X.Y.Z-rc.N       ->  P.2.N

où `P` est le plus grand numéro à trois éléments placé avant `X.Y.Z` :
`X.Y.(Z-1)` ; si Z vaut 0, `X.(Y-1).99999` ; si Y vaut 0 aussi,
`(X-1).99999.99999`. Ainsi `0.4.0-rc.12` s'écrit `0.3.99999.2.12` :
après `0.3.0` et tout correctif `0.3.z`, après `0.4.0-dev.N`, avant `0.4.0`.

La correspondance est une fonction, et elle s'inverse : le plugin relit dans le
registre de LibreOffice l'écriture numérique et rend au Device Management le
numéro qu'il connaît. Conséquences : aucun élément d'un numéro ne peut
atteindre 99999, et une version à cinq éléments dont le quatrième vaut 1 ou 2
est toujours lue comme une pré-version.

Module sans dépendance, exécutable : le build l'appelle pour inscrire le numéro.

    python3 src/mirai/package_version.py native 0.4.0-rc.12   # 0.3.99999.2.12
    python3 src/mirai/package_version.py dm 0.3.99999.2.12    # 0.4.0-rc.12
"""

import re
import sys

SENTINEL = 99999
RELEASE_CANDIDATE = "rc"
TRIAL = "dev"
_RANKS = {TRIAL: 1, RELEASE_CANDIDATE: 2}
_KINDS = {rank: kind for kind, rank in _RANKS.items()}

_DM_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-(rc|dev)\.(\d+))?$")
_NATIVE_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)\.([12])\.(\d+)$")


def _parse(version):
    """(X, Y, Z, nature, N) d'un numéro du Device Management, ou None."""
    match = _DM_RE.match(str(version or "").strip())
    if not match:
        return None
    major, minor, patch = (int(part) for part in match.group(1, 2, 3))
    if max(major, minor, patch) >= SENTINEL:
        return None
    kind = match.group(4) or ""
    if kind and (major, minor, patch) == (0, 0, 0):
        return None
    return major, minor, patch, kind, int(match.group(5) or 0)


def is_valid(version):
    """Vrai pour `X.Y.Z`, `X.Y.Z-rc.N` ou `X.Y.Z-dev.N` inscriptible dans un paquet."""
    return _parse(version) is not None


def prerelease_kind(version):
    """`"rc"`, `"dev"`, ou `""` pour une version générale ou un numéro inconnu."""
    parsed = _parse(version)
    return parsed[3] if parsed else ""


def native_version(version):
    """Écriture pour `description.xml`. Un numéro qui n'est pas une pré-version
    est rendu tel quel."""
    parsed = _parse(version)
    if not parsed or not parsed[3]:
        return str(version or "").strip()
    major, minor, patch, kind, number = parsed
    if patch:
        before = (major, minor, patch - 1)
    elif minor:
        before = (major, minor - 1, SENTINEL)
    else:
        before = (major - 1, SENTINEL, SENTINEL)
    return ".".join(str(part) for part in before + (_RANKS[kind], number))


def dm_version(native):
    """Numéro du Device Management pour une version lue dans LibreOffice. Ce qui
    n'est pas l'écriture d'une pré-version est rendu tel quel."""
    text = str(native or "").strip()
    match = _NATIVE_RE.match(text)
    if not match:
        return text
    major, minor, patch, rank, number = (int(part) for part in match.groups())
    if patch != SENTINEL:
        patch += 1
    elif minor != SENTINEL:
        minor, patch = minor + 1, 0
    else:
        major, minor, patch = major + 1, 0, 0
    return f"{major}.{minor}.{patch}-{_KINDS[rank]}.{number}"


def main(argv):
    if len(argv) != 2 or argv[0] not in ("native", "dm"):
        sys.stderr.write("usage: package_version.py native|dm <version>\n")
        return 2
    if argv[0] == "dm":
        print(dm_version(argv[1]))
        return 0
    if not is_valid(argv[1]):
        sys.stderr.write(f"Version refusée « {argv[1]} » : X.Y.Z, X.Y.Z-rc.N ou X.Y.Z-dev.N "
                         f"attendu, chaque élément sous {SENTINEL}.\n")
        return 1
    print(native_version(argv[1]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
