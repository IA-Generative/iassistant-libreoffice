# DM : feed natif LibreOffice et statut `deferred` — plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Le DM sert le feed `update.xml` au format LibreOffice pour le plugin `mirai-libreoffice` et cesse de compter le statut `deferred` comme un échec de campagne.

**Architecture:** Un endpoint public `GET /catalog/{slug}/update.xml` dans `app/main.py`, à côté du manifeste Chromium `updates.xml`, qui annonce la dernière version `published` du plugin avec l'identifiant OXT lu dans `plugins.extension_id`. Une ligne changée dans le mappage de `/update/status`. Tests unitaires avec psycopg2 factice et `TestClient`, comme les tests existants.

**Tech Stack:** Python 3.11, FastAPI, psycopg2 (mocké en test), pytest.

**Spec:** `docs/superpowers/specs/2026-09-22-maj-native-pilotee-dm-design.md` (dépôt AssistantMiraiLibreOffice), §4.

## Global Constraints

- Dépôt : `../device-management`, branche `feat/libreoffice-update-feed` créée depuis `main`.
- Style : ruff, `line-length = 120`, `target-version = py311`, règles `E F B UP I` (voir `pyproject.toml`).
- Tests : `python3 -m pytest -m "not integration" -q` doit rester vert ; nouveaux tests dans `tests/test_libreoffice_update_feed.py`.
- Aucune migration : `plugins.extension_id` existe déjà (migration 002).
- Le feed est anonyme et public : aucune authentification, aucune cohorte.
- Commits : message en français, format `type(scope): sujet`, trailer `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

---

### Task 0: Branche et base de tests

**Files:**
- Aucun fichier modifié.

- [ ] **Step 1: Créer la branche depuis `main`**

```bash
cd ../device-management
git status --short        # attendu : seulement ".planning/" non suivi
git checkout -b feat/libreoffice-update-feed main
```

- [ ] **Step 2: Vérifier que la suite tourne**

Run: `cd ../device-management && python3 -m pytest -m "not integration" -q 2>&1 | tail -3`
Expected: `N passed` (297 au 2026-08-24, éventuels tests instables listés dans les issues DM #21 et #22). Si `ModuleNotFoundError: fastapi`, créer un venv : `python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements-dev.txt`, puis relancer.

---

### Task 1: Endpoint `GET /catalog/{slug}/update.xml`

**Files:**
- Modify: `app/main.py` (après la fonction `catalog_updates_xml`, vers la ligne 4815)
- Create: `tests/test_libreoffice_update_feed.py`

**Interfaces:**
- Consumes: `_db_url_bootstrap()`, `_db_url()`, `_pooled_conn()`, `psycopg2`, `logger`, `HTTPException`, `Request`, `Response` (tous déjà dans `app/main.py`).
- Produces: route `GET /catalog/{slug}/update.xml` → `text/xml; charset=utf-8`, corps au namespace `http://openoffice.org/extensions/update/2006`.

- [ ] **Step 1: Écrire le fichier de test avec les cas 200 et 404**

Créer `tests/test_libreoffice_update_feed.py` :

```python
"""Feed natif LibreOffice (<update-information>) — GET /catalog/{slug}/update.xml.

Le plugin LibreOffice embarque, cuit au build, une liste d'URL de feed
`<bootstrap>/catalog/mirai-libreoffice/update.xml`. LibreOffice l'interroge
ANONYMEMENT avec sa propre pile HTTP : ni relay-headers, ni UUID client, donc
ni cohorte ni canary ici — le ciblage reste porté par la directive `update`
de /config. Le feed annonce la dernière version `published`.

Les interactions DB sont mockées (même approche que test_enriched_config.py).
"""
from __future__ import annotations

import importlib
import os
import sys
import types
import xml.etree.ElementTree as ET
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

NS_UPDATE = "http://openoffice.org/extensions/update/2006"
NS_XLINK = "http://www.w3.org/1999/xlink"
NS = {"u": NS_UPDATE}


def _setup_env() -> None:
    os.environ["DM_STORE_ENROLL_LOCALLY"] = "false"
    os.environ["DM_STORE_ENROLL_S3"] = "false"
    os.environ["DM_CONFIG_ENABLED"] = "true"
    os.environ["DM_CONFIG_PROFILE"] = "prod"
    os.environ["DM_RELAY_ENABLED"] = "false"
    os.environ["DM_AUTH_VERIFY_ACCESS_TOKEN"] = "false"
    os.environ["DM_TELEMETRY_ENABLED"] = "true"
    os.environ["DM_RELAY_REQUIRE_KEY_FOR_SECRETS"] = "false"
    os.environ["DATABASE_URL"] = "postgresql://dev:dev@localhost:5432/bootstrap"


def _load_module():
    _setup_env()
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if root not in sys.path:
        sys.path.insert(0, root)
    sys.modules.pop("app.main", None)
    sys.modules.pop("app.settings", None)

    fake_psycopg2 = types.ModuleType("psycopg2")
    fake_psycopg2.connect = MagicMock()
    fake_psycopg2.Error = Exception
    sys.modules["psycopg2"] = fake_psycopg2

    mod = importlib.import_module("app.main")
    importlib.reload(mod)
    mod.psycopg2 = fake_psycopg2
    return mod


def _make_cursor_mock(cursor_rows_by_query: dict) -> MagicMock:
    """Cursor mock : fetchone/fetchall répondent selon un fragment de la
    dernière requête SQL exécutée ; enregistre (sql, params) dans cur.calls."""
    cur = MagicMock()
    cur.calls = []
    _last_sql: list[str] = [""]

    def _execute(sql, params=None):
        _last_sql[0] = sql
        cur.calls.append((sql, params))

    def _fetchall():
        sql = _last_sql[0]
        for fragment, rows in cursor_rows_by_query.items():
            if fragment in sql:
                return list(rows)
        return []

    def _fetchone():
        rows = _fetchall()
        return rows[0] if rows else None

    cur.execute.side_effect = _execute
    cur.fetchall.side_effect = _fetchall
    cur.fetchone.side_effect = _fetchone
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    return cur


def _install_db_mock(mod, cursor_rows_by_query: dict):
    """Patch mod.psycopg2.connect → conn mock ; renvoie (patcher, cursor)."""
    cur = _make_cursor_mock(cursor_rows_by_query)
    conn = MagicMock()
    conn.autocommit = True
    conn.cursor.return_value = cur
    conn.__enter__ = lambda s: s
    conn.__exit__ = MagicMock(return_value=False)
    conn.close = MagicMock()
    patcher = patch.object(mod.psycopg2, "connect", return_value=conn)
    patcher.start()
    return patcher, cur


PLUGIN_ROW = (7, "libreoffice", "fr.gouv.interieur.mirai")   # id, device_type, extension_id
VERSION_ROW = ("0.0.1.0.32",)


def _get(mod, rows: dict, public_base: str | None = "https://dm.example/bootstrap"):
    patcher, cur = _install_db_mock(mod, rows)
    try:
        if public_base is None:
            os.environ.pop("PUBLIC_BASE_URL", None)
        else:
            os.environ["PUBLIC_BASE_URL"] = public_base
        client = TestClient(mod.app)
        return client.get("/catalog/mirai-libreoffice/update.xml"), cur
    finally:
        patcher.stop()
        os.environ.pop("PUBLIC_BASE_URL", None)


def test_update_xml_announces_latest_published_version():
    mod = _load_module()
    res, _cur = _get(mod, {
        "extension_id FROM plugins": [PLUGIN_ROW],
        "FROM plugin_versions pv": [VERSION_ROW],
    })
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith("text/xml")
    assert res.headers.get("cache-control") == "no-cache"

    root = ET.fromstring(res.content)
    assert root.tag == f"{{{NS_UPDATE}}}description"
    assert root.find("u:identifier", NS).get("value") == "fr.gouv.interieur.mirai"
    assert root.find("u:version", NS).get("value") == "0.0.1.0.32"
    src = root.find("u:update-download/u:src", NS)
    assert src.get(f"{{{NS_XLINK}}}href") == (
        "https://dm.example/bootstrap/catalog/mirai-libreoffice/download/mirai-libreoffice-0.0.1.0.32.oxt"
    )


def test_update_xml_only_queries_published_versions():
    """Les versions expérimentales/taguées ne sont jamais annoncées : la requête
    filtre sur status = 'published' (comme /catalog/{slug}/download sans tag)."""
    mod = _load_module()
    _res, cur = _get(mod, {
        "extension_id FROM plugins": [PLUGIN_ROW],
        "FROM plugin_versions pv": [VERSION_ROW],
    })
    version_sql = [sql for sql, _ in cur.calls if "FROM plugin_versions pv" in sql]
    assert version_sql, "requête versions jamais exécutée"
    assert "pv.status = 'published'" in version_sql[-1]
    assert "tag" not in version_sql[-1]


def test_update_xml_falls_back_to_request_base_url_in_https():
    mod = _load_module()
    res, _cur = _get(mod, {
        "extension_id FROM plugins": [PLUGIN_ROW],
        "FROM plugin_versions pv": [VERSION_ROW],
    }, public_base=None)
    assert res.status_code == 200, res.text
    src = ET.fromstring(res.content).find("u:update-download/u:src", NS)
    href = src.get(f"{{{NS_XLINK}}}href")
    assert href.startswith("https://testserver/"), href   # http → https, sauf localhost
    assert href.endswith("/catalog/mirai-libreoffice/download/mirai-libreoffice-0.0.1.0.32.oxt")


def test_update_xml_404_unknown_slug():
    mod = _load_module()
    res, _cur = _get(mod, {"extension_id FROM plugins": []})
    assert res.status_code == 404


def test_update_xml_404_for_non_libreoffice_plugin():
    mod = _load_module()
    res, _cur = _get(mod, {
        "extension_id FROM plugins": [(8, "firefox", "matisse@interieur.gouv.fr")],
        "FROM plugin_versions pv": [VERSION_ROW],
    })
    assert res.status_code == 404


def test_update_xml_404_without_published_version():
    mod = _load_module()
    res, _cur = _get(mod, {
        "extension_id FROM plugins": [PLUGIN_ROW],
        "FROM plugin_versions pv": [],
    })
    assert res.status_code == 404


def test_update_xml_404_when_extension_id_missing():
    """Sans identifiant OXT sur la fiche plugin, LibreOffice ignorerait le feed :
    on répond 404 (et un avertissement en log) plutôt qu'un feed inutilisable."""
    mod = _load_module()
    res, _cur = _get(mod, {
        "extension_id FROM plugins": [(7, "libreoffice", None)],
        "FROM plugin_versions pv": [VERSION_ROW],
    })
    assert res.status_code == 404
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `cd ../device-management && python3 -m pytest tests/test_libreoffice_update_feed.py -q 2>&1 | tail -5`
Expected: 3 failed (`test_update_xml_announces_latest_published_version`, `test_update_xml_only_queries_published_versions`, `test_update_xml_falls_back_to_request_base_url_in_https`, en `assert 404 == 200` ou `AssertionError`) et 4 passed : la route n'existe pas encore, FastAPI répond 404 partout, donc les tests 404 passent par accident. Attendu à ce stade ; ils protègent contre une régression future.

- [ ] **Step 3: Ajouter l'import `quoteattr` en tête de `app/main.py`**

Vérifier d'abord : `grep -n "quoteattr\|saxutils" app/main.py`. Si absent, ajouter dans le bloc des imports stdlib (ordre isort) :

```python
from xml.sax.saxutils import quoteattr
```

- [ ] **Step 4: Ajouter la route après `catalog_updates_xml`**

Insérer juste après la fin de `catalog_updates_xml` (avant le commentaire `# ─── Auto-update multi-format / multi-cible (DM-4)`) :

```python
_LO_UPDATE_NS = "http://openoffice.org/extensions/update/2006"


@app.get("/catalog/{slug}/update.xml")
def catalog_libreoffice_update_xml(request: Request, slug: str):
    """Feed natif LibreOffice (<update-information>) pour un plugin `.oxt`.

    Public et anonyme : LibreOffice l'interroge avec sa propre pile HTTP, sans
    relay-headers ni UUID client — pas de cohorte ni de canary ici, le ciblage
    reste porté par la directive `update` de /config. Annonce la dernière
    version `published` (jamais une version expérimentale/taguée) avec l'URL
    versionnée de l'OXT. L'identifiant OXT est `plugins.extension_id`.
    Ne pas confondre avec /catalog/{slug}/updates.xml, le manifeste Chromium.
    """
    db_url = _db_url_bootstrap() or _db_url()
    if not psycopg2 or not db_url:
        raise HTTPException(404, "Plugin introuvable")
    conn = None
    pool_ctx = _pooled_conn()
    try:
        if pool_ctx is not None:
            conn = pool_ctx.__enter__()
        else:
            conn = psycopg2.connect(db_url)
            conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, device_type, extension_id FROM plugins WHERE slug = %s AND status = 'active'",
                (slug,),
            )
            prow = cur.fetchone()
            if not prow or prow[1] != "libreoffice":
                raise HTTPException(404, "Plugin LibreOffice introuvable")
            plugin_id, _device_type, extension_id = prow
            if not extension_id:
                logger.warning(
                    "update.xml: plugins.extension_id vide pour %s — renseigner l'identifiant OXT "
                    "sur la fiche plugin (ex. fr.gouv.interieur.mirai)", slug)
                raise HTTPException(404, "Identifiant d'extension non renseigné")
            cur.execute("""
                SELECT pv.version FROM plugin_versions pv
                WHERE pv.plugin_id = %s AND pv.status = 'published'
                ORDER BY pv.published_at DESC NULLS LAST LIMIT 1
            """, (plugin_id,))
            vrow = cur.fetchone()
            if not vrow:
                raise HTTPException(404, "Aucune version publiée")
            version = str(vrow[0])
    finally:
        if pool_ctx is not None:
            pool_ctx.__exit__(None, None, None)
        elif conn is not None:
            conn.close()

    base = (os.getenv("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if not base:
        base = str(request.base_url).rstrip("/")
        if base.startswith("http://") and "localhost" not in base:
            base = "https://" + base[len("http://"):]
    download = f"{base}/catalog/{slug}/download/{slug}-{version}.oxt"
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<description xmlns="{_LO_UPDATE_NS}"\n'
        '             xmlns:xlink="http://www.w3.org/1999/xlink">\n'
        f"  <identifier value={quoteattr(str(extension_id))}/>\n"
        f"  <version value={quoteattr(version)}/>\n"
        "  <update-download>\n"
        f"    <src xlink:href={quoteattr(download)}/>\n"
        "  </update-download>\n"
        "</description>\n"
    )
    return Response(
        content=xml,
        media_type="text/xml; charset=utf-8",
        headers={"Cache-Control": "no-cache"},
    )
```

- [ ] **Step 5: Lancer les tests du feed**

Run: `cd ../device-management && python3 -m pytest tests/test_libreoffice_update_feed.py -q 2>&1 | tail -3`
Expected: `7 passed`

- [ ] **Step 6: Lint et suite complète**

Run: `cd ../device-management && ruff check app/main.py tests/test_libreoffice_update_feed.py && python3 -m pytest -m "not integration" -q 2>&1 | tail -3`
Expected: ruff sans erreur ; même nombre de `passed` qu'en Task 0 plus 7.

- [ ] **Step 7: Commit**

```bash
cd ../device-management
git add app/main.py tests/test_libreoffice_update_feed.py
git commit -m "feat(catalog): feed natif LibreOffice /catalog/{slug}/update.xml (#4)

Format http://openoffice.org/extensions/update/2006 : identifier =
plugins.extension_id, version = dernière version published, src = URL
versionnée de l'OXT (PUBLIC_BASE_URL). Anonyme : le ciblage cohorte/canary
reste porté par la directive update de /config.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: `/update/status` — `deferred` compté comme `notified`

**Files:**
- Modify: `app/main.py` (fonction `_update_campaign_device_status_sync`, ligne `db_status = "updated" if status == "installed" else "failed"`)
- Test: `tests/test_libreoffice_update_feed.py` (ajout en fin de fichier)

**Interfaces:**
- Consumes: `_update_campaign_device_status_sync(*, campaign_id, client_uuid, status, version_before, version_after, error_detail)`.
- Produces: même signature ; valeur SQL `notified` pour `status == "deferred"`.

- [ ] **Step 1: Écrire le test**

Ajouter en fin de `tests/test_libreoffice_update_feed.py` :

```python
# ── /update/status : « deferred » n'est pas un échec ─────────────────────
# Le plugin (>= fix/MAJ) rapporte « deferred » au staging ou à l'ouverture du
# dialogue natif, puis « installed » à la réconciliation après redémarrage.
# Compter « deferred » en failed gonflait failure_rate entre les deux phases.

def _status_param(mod, status: str) -> str:
    patcher, cur = _install_db_mock(mod, {})
    try:
        mod._update_campaign_device_status_sync(
            campaign_id=42, client_uuid="uuid-1", status=status,
            version_before="0.0.1.0.31", version_after="0.0.1.0.32", error_detail="",
        )
    finally:
        patcher.stop()
    inserts = [params for sql, params in cur.calls if "INSERT INTO campaign_device_status" in sql]
    assert inserts, "aucun upsert de statut exécuté"
    return inserts[-1][2]


def test_update_status_deferred_maps_to_notified():
    mod = _load_module()
    assert _status_param(mod, "deferred") == "notified"


def test_update_status_installed_and_failures_unchanged():
    mod = _load_module()
    assert _status_param(mod, "installed") == "updated"
    assert _status_param(mod, "failed") == "failed"
    assert _status_param(mod, "checksum_error") == "failed"
    assert _status_param(mod, "download_error") == "failed"
```

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `cd ../device-management && python3 -m pytest tests/test_libreoffice_update_feed.py -q -k update_status 2>&1 | tail -5`
Expected: `test_update_status_deferred_maps_to_notified` FAIL (`'failed' == 'notified'`), l'autre PASS.

- [ ] **Step 3: Changer le mappage**

Dans `_update_campaign_device_status_sync`, remplacer :

```python
            # Map plugin status to DB enum
            db_status = "updated" if status == "installed" else "failed"
```

par :

```python
            # Map plugin status to DB enum. « deferred » = artefact stagé ou
            # dialogue natif montré, installation à suivre après redémarrage :
            # ce n'est pas un échec (le plugin rapporte « installed » à la
            # réconciliation) — le compter en failed faussait failure_rate.
            db_status = {"installed": "updated", "deferred": "notified"}.get(status, "failed")
```

- [ ] **Step 4: Lancer les tests**

Run: `cd ../device-management && python3 -m pytest tests/test_libreoffice_update_feed.py -q 2>&1 | tail -3`
Expected: `9 passed`

- [ ] **Step 5: Lint, suite complète, commit**

Run: `cd ../device-management && ruff check app/main.py tests/test_libreoffice_update_feed.py && python3 -m pytest -m "not integration" -q 2>&1 | tail -3`
Expected: vert.

```bash
cd ../device-management
git add app/main.py tests/test_libreoffice_update_feed.py
git commit -m "fix(campaigns): /update/status compte « deferred » en notified, pas en failed

Le plugin rapporte deferred au staging puis installed à la réconciliation
post-redémarrage ; entre les deux, failure_rate était gonflé à tort.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Documentation développeur du feed

**Files:**
- Modify: `docs/plugin-developer/plugin-dm-protocol-update-features.md` (nouvelle section après « 6 bis. Canal de retrait (pull) », avant « 7. Flux de mise à jour dans le plugin »)

- [ ] **Step 1: Insérer la section**

```markdown
## 6 ter. Feed natif LibreOffice — `GET /catalog/{slug}/update.xml`

Réservé aux plugins `device_type = libreoffice`. LibreOffice sait vérifier et
installer lui-même les mises à jour d'une extension dont le `description.xml`
déclare un bloc `<update-information>` : le bouton « Vérifier les mises à jour »
du Gestionnaire des extensions, et le déclenchement programmatique
(`com.sun.star.deployment.ui.PackageManagerDialog`, `trigger("SHOW_UPDATE_DIALOG")`)
interrogent ce feed, téléchargent l'OXT et l'installent dans le processus soffice.

**Format servi** (namespace obligatoire) :

```xml
<?xml version="1.0" encoding="UTF-8"?>
<description xmlns="http://openoffice.org/extensions/update/2006"
             xmlns:xlink="http://www.w3.org/1999/xlink">
  <identifier value="fr.gouv.interieur.mirai"/>
  <version value="0.0.1.0.32"/>
  <update-download>
    <src xlink:href="https://<dm>/catalog/mirai-libreoffice/download/mirai-libreoffice-0.0.1.0.32.oxt"/>
  </update-download>
</description>
```

| Élément | Source |
|---|---|
| `identifier` | `plugins.extension_id` — **à renseigner sur la fiche plugin** avec l'identifiant du `description.xml` de l'OXT ; vide → 404 |
| `version` | dernière `plugin_versions.status = 'published'` ; les versions expérimentales ou taguées ne sont jamais annoncées |
| `src` | URL versionnée de l'OXT, bâtie sur `PUBLIC_BASE_URL` |

**Ce que ce feed n'est pas.** Il est public et anonyme : LibreOffice le lit avec sa
propre pile HTTP, sans relay-headers ni `X-Client-UUID`. Le DM ne peut donc ni
cibler une cohorte ni appliquer un palier canary sur cette route ; tout poste qui
interroge un DM voit la même version. Le ciblage reste porté par la directive
`update` de `/config` (§ 4.3) : côté plugin, la route native n'est empruntée que si
la version annoncée par le feed est exactement `target_version`. Ne pas confondre
avec `/catalog/{slug}/updates.xml` (manifeste Chromium `gupdate`) ni
`/updates/{slug}/{target}.json` (manifeste Gecko).

**Sémantique de `/update/status` (§ 8)** : `deferred` = artefact stagé ou dialogue
natif ouvert, installation à suivre ; il est enregistré `notified`, pas `failed`.
`installed` n'est rapporté par le plugin qu'une fois la nouvelle version réellement
active, au redémarrage suivant.
```

- [ ] **Step 2: Relire le rendu et commit**

Run: `cd ../device-management && grep -n "6 ter" docs/plugin-developer/plugin-dm-protocol-update-features.md`
Expected: une ligne, entre « 6 bis » et « 7. ».

```bash
cd ../device-management
git add docs/plugin-developer/plugin-dm-protocol-update-features.md
git commit -m "docs(plugin-developer): contrat du feed natif LibreOffice et sémantique deferred

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Hors plan

Remplissage automatique de `extension_id` à l'upload d'un OXT, affichage de l'URL du
feed dans l'admin, `<release-notes>` : décisions séparées (spec §9).
