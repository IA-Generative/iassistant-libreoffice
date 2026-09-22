# Plugin : mise à jour pilotée par le DM, route native d'abord — plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sur directive du DM, le plugin ouvre le dialogue natif « Mise à jour des extensions » de LibreOffice, qui télécharge et installe l'OXT lui-même ; la route dirigée de fix/MAJ reste le repli, et un refus n'est plus reproposé avant 24 h.

**Architecture:** Tout part de `origin/fix/MAJ`. Un seul primitif d'installation, `addExtension` sur le thread principal ; la route native y arrive via `PackageManagerDialog.trigger("SHOW_UPDATE_DIALOG")`, la route dirigée via le code existant. L'état persistant `pending_update/update_state.json` gagne `route`, `postponed_until`, `native_attempts` ; la réconciliation au redémarrage ne change pas.

**Tech Stack:** Python 3 stdlib embarqué dans LibreOffice, pyuno, stubs UNO de `tests/stubs/uno_stubs.py`, pytest.

**Spec:** `docs/superpowers/specs/2026-09-22-maj-native-pilotee-dm-design.md`, §3, §5, §6.

## Global Constraints

- Branche `feat/maj-native-pilotee` créée depuis `origin/fix/MAJ` (jamais depuis `develop`).
- Aucun paquet pip : stdlib et pyuno seulement.
- Depuis le thread worker, aucun `from com.sun.star… import` : accès aux singletons par `ctx.getValueByName`, aux services par `createInstanceWithContext`, exécution sur le thread principal par `AsyncCallback` + `_MainThreadCallback` (déjà dans fix/MAJ).
- `processEventsToIdle` interdit dans `core/` et `ui/` ; règle testée par `tests/unit/test_pump_events_safety.py` et `test_core_and_ui_never_pump_events`, à garder vertes.
- Aucun processus enfant, aucun script par défaut.
- Tout le code de mise à jour vit dans `src/mirai/entrypoint.py`, classe `MainJob` ; commentaires en français, style des fonctions voisines.
- Tests : `python3 -m pytest tests/unit/test_native_update.py tests/unit/test_native_route.py tests/unit/test_update_features.py tests/unit/test_update_blocked.py -q` vert ; puis la suite complète `python3 -m pytest tests/unit/ tests/integration/ -q` sans nouvel échec par rapport à la base (Task 0).
- Constantes de la spec §5.1 : `_UPDATE_POSTPONE_SECONDS = 24 * 3600`, `_NATIVE_INSTALL_WAIT_SECONDS = 900`, `_NATIVE_POLL_SECONDS = 5`, `_NATIVE_TRIGGER_TIMEOUT_SECONDS = 30`, `_NATIVE_MAX_ATTEMPTS = 2`.
- Commits : message en français, `type(scope): sujet`, trailer `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

---

### Task 0: Branche, spec, base de tests

**Files:**
- Create: `docs/superpowers/specs/2026-09-22-maj-native-pilotee-dm-design.md` (copie depuis l'arbre `develop`)
- Create: `docs/superpowers/plans/2026-09-22-plugin-maj-native-pilotee.md`, `docs/superpowers/plans/2026-09-22-dm-libreoffice-update-feed.md` (idem)

- [ ] **Step 1: Créer la branche dans un worktree isolé**

Suivre `superpowers:using-git-worktrees`. Point de départ obligatoire : `origin/fix/MAJ`.

```bash
git fetch origin
git worktree add .worktrees/maj-native-pilotee -b feat/maj-native-pilotee origin/fix/MAJ
cd .worktrees/maj-native-pilotee
git log --oneline -1     # attendu : e97bb4e Update README.md (tête de fix/MAJ)
```

- [ ] **Step 2: Copier la spec et les plans dans le worktree, commit**

```bash
mkdir -p docs/superpowers/specs docs/superpowers/plans
cp ../../docs/superpowers/specs/2026-09-22-maj-native-pilotee-dm-design.md docs/superpowers/specs/
cp ../../docs/superpowers/plans/2026-09-22-*.md docs/superpowers/plans/
git add docs/superpowers
git commit -m "docs(update): spec et plans — MAJ pilotée par le DM, route native d'abord (#9, #5)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

- [ ] **Step 3: Base de tests**

Run: `python3 -m pytest tests/unit/test_native_update.py tests/unit/test_update_features.py tests/unit/test_update_blocked.py -q 2>&1 | tail -3`
Expected: vert (fix/MAJ). Noter le résultat de la suite complète pour comparaison finale :
`python3 -m pytest tests/unit/ tests/integration/ -q 2>&1 | tail -3` (au 2026-09-21 sur develop : 7 failed dont 6 `summarize_writer` préexistants, 806 passed ; fix/MAJ les désélectionne en CI).

---

### Task 1: État persistant — `route`, `postponed_until`, `native_attempts`, `version_before` stable

**Files:**
- Modify: `src/mirai/entrypoint.py` — constantes après `_UPDATE_FEED_NS` (ligne 29) ; méthode `_save_update_state` (vers la ligne 2738) ; nouvelle méthode `_load_update_state` juste avant.
- Create: `tests/unit/test_native_route.py`

**Interfaces:**
- Produces: `MainJob._load_update_state() -> dict` (vide si absent ou illisible) ; `MainJob._save_update_state(directive, stage, route=None, postponed_until=None, native_attempts=None)` ; constantes module `_UPDATE_POSTPONE_SECONDS`, `_NATIVE_INSTALL_WAIT_SECONDS`, `_NATIVE_POLL_SECONDS`, `_NATIVE_TRIGGER_TIMEOUT_SECONDS`, `_NATIVE_MAX_ATTEMPTS`.

- [ ] **Step 1: Écrire les tests d'état**

Créer `tests/unit/test_native_route.py` :

```python
"""Route native pilotée par le DM (spec 2026-09-22, issue #9) : le DM décide
(directive), LibreOffice installe (dialogue « Mise à jour des extensions »,
feed <update-information>), le plugin ferme proprement. Route dirigée de
fix/MAJ en repli ; refus mémorisé 24 h.

Run:  pytest tests/unit/test_native_route.py -v
"""
import json
import os
import tempfile
import threading
import time
from unittest.mock import MagicMock

from tests.stubs.uno_stubs import install, make_job

install()

from src.mirai import entrypoint
from src.mirai.entrypoint import MainJob

TARGET = "0.0.1.0.32"
CURRENT = "0.0.1.0.31"


def _job(current_version=CURRENT):
    job = make_job(config_dir=tempfile.mkdtemp())
    job._get_extension_version = MagicMock(return_value=current_version)
    job._report_update_status = MagicMock()
    job._send_telemetry = MagicMock()
    return job


def _state(job):
    with open(job._update_state_path(), encoding="utf-8") as fh:
        return json.load(fh)


# ── État persistant ──────────────────────────────────────────────────────

def test_constants_match_spec():
    assert entrypoint._UPDATE_POSTPONE_SECONDS == 24 * 3600
    assert entrypoint._NATIVE_INSTALL_WAIT_SECONDS == 900
    assert entrypoint._NATIVE_POLL_SECONDS == 5
    assert entrypoint._NATIVE_TRIGGER_TIMEOUT_SECONDS == 30
    assert entrypoint._NATIVE_MAX_ATTEMPTS == 2


def test_save_update_state_persists_route_and_cooldown_fields():
    job = _job()
    until = time.time() + 3600
    job._save_update_state({"campaign_id": 3, "target_version": TARGET}, "native_dialog",
                           route="native", postponed_until=until, native_attempts=1)
    state = _state(job)
    assert state["route"] == "native"
    assert state["postponed_until"] == until
    assert state["native_attempts"] == 1
    assert state["stage"] == "native_dialog"


def test_save_update_state_keeps_fields_for_same_target():
    """Un enregistrement d'étape ne perd pas route / native_attempts posés avant."""
    job = _job()
    d = {"campaign_id": 3, "target_version": TARGET}
    job._save_update_state(d, "native_dialog", route="native", native_attempts=1)
    job._save_update_state(d, "postponed", postponed_until=time.time() + 10)
    state = _state(job)
    assert state["route"] == "native"
    assert state["native_attempts"] == 1
    assert state["postponed_until"] > time.time()


def test_save_update_state_resets_fields_for_new_target():
    job = _job()
    job._save_update_state({"campaign_id": 3, "target_version": TARGET}, "postponed",
                           route="native", postponed_until=time.time() + 10, native_attempts=2)
    job._save_update_state({"campaign_id": 4, "target_version": "0.0.1.0.33"}, "staged")
    state = _state(job)
    assert state["target_version"] == "0.0.1.0.33"
    assert state["route"] == ""
    assert state["postponed_until"] == 0
    assert state["native_attempts"] == 0


def test_save_update_state_keeps_version_before_across_stages():
    """Après l'installation, la version active est déjà la cible : version_before
    doit rester celle d'avant, sinon la réconciliation rapporte n'importe quoi."""
    job = _job()
    d = {"campaign_id": 3, "target_version": TARGET}
    job._save_update_state(d, "native_dialog", route="native")
    job._get_extension_version = MagicMock(return_value=TARGET)
    job._save_update_state(d, "installed_native")
    assert _state(job)["version_before"] == CURRENT


def test_load_update_state_returns_empty_when_missing_or_corrupt():
    job = _job()
    assert job._load_update_state() == {}
    os.makedirs(os.path.dirname(job._update_state_path()), exist_ok=True)
    with open(job._update_state_path(), "w") as fh:
        fh.write("{not json")
    assert job._load_update_state() == {}
```

- [ ] **Step 2: Vérifier l'échec**

Run: `python3 -m pytest tests/unit/test_native_route.py -q 2>&1 | tail -5`
Expected: 6 failed (`AttributeError: … has no attribute '_UPDATE_POSTPONE_SECONDS'` / `_load_update_state`, `TypeError: unexpected keyword argument 'route'`).

- [ ] **Step 3: Ajouter les constantes**

Après la ligne `_UPDATE_FEED_NS = "http://openoffice.org/extensions/update/2006"` :

```python
# Route native pilotée (spec 2026-09-22) : refus mémorisé, attente de
# l'installation par LibreOffice, bornes du déclenchement.
_UPDATE_POSTPONE_SECONDS = 24 * 3600
_NATIVE_INSTALL_WAIT_SECONDS = 900
_NATIVE_POLL_SECONDS = 5
_NATIVE_TRIGGER_TIMEOUT_SECONDS = 30
_NATIVE_MAX_ATTEMPTS = 2
```

- [ ] **Step 4: Remplacer `_save_update_state` et ajouter `_load_update_state`**

Remplacer intégralement la méthode `_save_update_state` par :

```python
    def _load_update_state(self):
        """État persistant de la MAJ en cours, ou {} (fichier absent ou illisible).
        La réconciliation garde sa propre lecture, qui supprime un fichier corrompu."""
        path = self._update_state_path()
        if not path or not os.path.isfile(path):
            return {}
        try:
            with open(path, encoding="utf-8") as fh:
                state = json.load(fh)
        except Exception:
            return {}
        return state if isinstance(state, dict) else {}

    def _save_update_state(self, directive, stage, route=None, postponed_until=None,
                           native_attempts=None):
        """Persiste l'état de la campagne en cours (best-effort, jamais bloquant).

        Pour une même cible, les champs non fournis (route, postponed_until,
        native_attempts) et version_before sont conservés depuis l'état
        précédent : après l'installation, la version active est déjà la cible,
        et version_before doit rester celle d'avant pour la réconciliation.
        """
        path = self._update_state_path()
        if not path:
            return
        try:
            previous = self._load_update_state()
            target = str(directive.get("target_version", ""))
            same = bool(previous) and str(previous.get("target_version", "")) == target
            version_before = str(previous.get("version_before") or "") if same else ""
            if not version_before:
                version_before = str(self._get_extension_version() or "")
            if route is None:
                route = str(previous.get("route") or "") if same else ""
            if postponed_until is None:
                postponed_until = float(previous.get("postponed_until") or 0) if same else 0.0
            if native_attempts is None:
                native_attempts = int(previous.get("native_attempts") or 0) if same else 0
            state = {
                "campaign_id": directive.get("campaign_id"),
                "target_version": target,
                "version_before": version_before,
                "stage": stage,
                "ts": time.time(),
                "route": route,
                "postponed_until": float(postponed_until),
                "native_attempts": int(native_attempts),
            }
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(state, fh)
        except Exception as exc:
            log_to_file(f"_save_update_state: {exc}")
```

- [ ] **Step 5: Lancer les tests**

Run: `python3 -m pytest tests/unit/test_native_route.py tests/unit/test_native_update.py -q 2>&1 | tail -3`
Expected: vert, y compris `test_save_update_state_roundtrip` existant.

- [ ] **Step 6: Commit**

```bash
git add src/mirai/entrypoint.py tests/unit/test_native_route.py
git commit -m "feat(update): état persistant — route, cooldown, tentatives natives, version_before stable (#9)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: `_schedule_update` — une cible reportée n'est pas reproposée avant échéance

**Files:**
- Modify: `src/mirai/entrypoint.py` — `_schedule_update` (vers la ligne 1880), après le test `_update_launch_blocked_cls`.
- Test: `tests/unit/test_native_route.py`

**Interfaces:**
- Consumes: `_load_update_state()` (Task 1).

- [ ] **Step 1: Écrire les tests**

Ajouter à `tests/unit/test_native_route.py` :

```python
# ── Cooldown : une cible refusée ou ignorée n'est pas reproposée avant 24 h ──

def _schedule_and_wait(job, directive, seconds=0.5):
    done = threading.Event()
    job._perform_update = lambda d: done.set()
    job._schedule_update(directive)
    return done.wait(seconds)


def test_schedule_update_skips_target_in_cooldown():
    job = _job()
    job._save_update_state({"campaign_id": 3, "target_version": TARGET}, "postponed",
                           route="native", postponed_until=time.time() + 3600)
    assert _schedule_and_wait(job, {"action": "update", "target_version": TARGET}) is False


def test_schedule_update_runs_after_cooldown_expired():
    job = _job()
    job._save_update_state({"campaign_id": 3, "target_version": TARGET}, "postponed",
                           route="native", postponed_until=time.time() - 1)
    assert _schedule_and_wait(job, {"action": "update", "target_version": TARGET}) is True


def test_schedule_update_ignores_cooldown_of_other_target():
    job = _job()
    job._save_update_state({"campaign_id": 3, "target_version": TARGET}, "postponed",
                           route="native", postponed_until=time.time() + 3600)
    assert _schedule_and_wait(job, {"action": "update", "target_version": "0.0.1.0.33"}) is True
```

- [ ] **Step 2: Vérifier l'échec**

Run: `python3 -m pytest tests/unit/test_native_route.py -q -k cooldown 2>&1 | tail -3`
Expected: `test_schedule_update_skips_target_in_cooldown` FAIL (`assert True is False`), les deux autres PASS.

- [ ] **Step 3: Ajouter la garde**

Dans `_schedule_update`, juste après le bloc qui teste `_update_launch_blocked_cls` (qui se termine par `return`), insérer :

```python
        # Refus ou report mémorisé (route native ou dirigée) : ne pas reproposer
        # ni retélécharger à chaque rafraîchissement de config avant l'échéance.
        state = self._load_update_state()
        if target_version and str(state.get("target_version", "")) == target_version:
            until = float(state.get("postponed_until") or 0)
            if until > time.time():
                log_to_file(
                    f"Update skipped: {target_version} postponed until "
                    f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(until))}"
                )
                return
```

- [ ] **Step 4: Lancer les tests et committer**

Run: `python3 -m pytest tests/unit/test_native_route.py tests/unit/test_native_update.py tests/unit/test_update_features.py tests/unit/test_update_blocked.py -q 2>&1 | tail -3`
Expected: vert.

```bash
git add src/mirai/entrypoint.py tests/unit/test_native_route.py
git commit -m "feat(update): cooldown — une cible refusée n'est pas reproposée avant 24 h (#9)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: `_native_feed_offers` — le feed installé annonce-t-il exactement la cible ?

**Files:**
- Modify: `src/mirai/entrypoint.py` — nouvelle méthode juste avant `_update_feed_urls` (vers la ligne 2841).
- Test: `tests/unit/test_native_route.py`

**Interfaces:**
- Consumes: `_EXTENSION_IDENTIFIER` (module), `self.ctx.getValueByName`.
- Produces: `MainJob._native_feed_offers(target_version: str) -> bool`.

- [ ] **Step 1: Écrire les tests**

```python
# ── _native_feed_offers : PackageInformationProvider.isUpdateAvailable ────
# C'est LibreOffice qui interroge le feed cuit dans l'extension INSTALLÉE, avec
# sa pile HTTP. Vrai seulement si la version annoncée == cible de la directive.

PIP_PATH = "/singletons/com.sun.star.deployment.PackageInformationProvider"


def _job_with_provider(pairs=None, error=None):
    job = _job()
    provider = MagicMock(name="PackageInformationProvider")
    if error is not None:
        provider.isUpdateAvailable.side_effect = error
    else:
        provider.isUpdateAvailable.return_value = tuple(pairs or ())
    job.ctx.getValueByName = MagicMock(return_value=provider)
    return job, provider


def test_native_feed_offers_true_when_announced_version_matches():
    job, provider = _job_with_provider([("fr.gouv.interieur.mirai", TARGET)])
    assert job._native_feed_offers(TARGET) is True
    job.ctx.getValueByName.assert_called_with(PIP_PATH)
    provider.isUpdateAvailable.assert_called_once_with("fr.gouv.interieur.mirai")


def test_native_feed_offers_false_when_version_differs():
    job, _ = _job_with_provider([("fr.gouv.interieur.mirai", "0.0.1.0.40")])
    assert job._native_feed_offers(TARGET) is False


def test_native_feed_offers_false_when_feed_silent():
    """Bloc feed absent, feed injoignable ou pas plus récent → séquence vide."""
    job, _ = _job_with_provider([])
    assert job._native_feed_offers(TARGET) is False


def test_native_feed_offers_ignores_other_extensions():
    job, _ = _job_with_provider([("org.example.other", TARGET)])
    assert job._native_feed_offers(TARGET) is False


def test_native_feed_offers_false_on_error_or_missing_provider():
    job, _ = _job_with_provider(error=RuntimeError("proxy"))
    assert job._native_feed_offers(TARGET) is False
    job2 = _job()
    job2.ctx.getValueByName = MagicMock(return_value=None)
    assert job2._native_feed_offers(TARGET) is False
    assert job2._native_feed_offers("") is False
```

- [ ] **Step 2: Vérifier l'échec**

Run: `python3 -m pytest tests/unit/test_native_route.py -q -k native_feed_offers 2>&1 | tail -3`
Expected: 5 failed, `AttributeError: … '_native_feed_offers'`.

- [ ] **Step 3: Implémenter**

Insérer avant `def _update_feed_urls(self):` :

```python
    # ── Route native pilotée (spec 2026-09-22, issue #9) ─────────────────
    # Le DM décide (directive update), LibreOffice installe (dialogue « Mise à
    # jour des extensions »). Le plugin ne télécharge rien : il vérifie que le
    # feed <update-information> de l'extension installée annonce exactement la
    # cible, ouvre le dialogue natif sur le thread principal, puis surveille la
    # version installée et ferme LibreOffice proprement.

    def _native_feed_offers(self, target_version):
        """Vrai si le feed de l'extension INSTALLÉE annonce exactement
        target_version. Interrogé par LibreOffice lui-même
        (PackageInformationProvider.isUpdateAvailable, pile HTTP de LO — le
        même chemin que son contrôle périodique). Faux si bloc feed absent,
        feed injoignable, version divergente, ou erreur : la route dirigée prend
        alors le relais. Singleton obtenu sans import (thread worker)."""
        target = str(target_version or "").strip()
        if not target:
            return False
        try:
            provider = self.ctx.getValueByName(
                "/singletons/com.sun.star.deployment.PackageInformationProvider")
        except Exception as exc:
            log_to_file(f"_native_feed_offers: provider unavailable: {exc}")
            return False
        if provider is None:
            return False
        try:
            pairs = provider.isUpdateAvailable(_EXTENSION_IDENTIFIER)
        except Exception as exc:
            log_to_file(f"_native_feed_offers: isUpdateAvailable failed: {exc}")
            return False
        announced = ""
        for pair in pairs or ():
            try:
                ident, version = str(pair[0]), str(pair[1])
            except Exception:
                continue
            if ident == _EXTENSION_IDENTIFIER:
                announced = version
                break
        offers = announced == target
        log_to_file(
            f"_native_feed_offers: target={target} announced={announced or '-'} offers={offers}")
        return offers
```

- [ ] **Step 4: Lancer les tests et committer**

Run: `python3 -m pytest tests/unit/test_native_route.py -q 2>&1 | tail -3`
Expected: vert.

```bash
git add src/mirai/entrypoint.py tests/unit/test_native_route.py
git commit -m "feat(update): _native_feed_offers — le feed installé annonce-t-il la cible ? (#9, #5)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: `_trigger_native_update_dialog` — ouvrir le dialogue natif sur le thread principal

**Files:**
- Modify: `src/mirai/entrypoint.py` — nouvelle méthode après `_native_feed_offers`.
- Test: `tests/unit/test_native_route.py`

**Interfaces:**
- Consumes: `_MainThreadCallback` (module, peut valoir `None` hors LibreOffice), service `com.sun.star.awt.AsyncCallback`.
- Produces: `MainJob._trigger_native_update_dialog(timeout=_NATIVE_TRIGGER_TIMEOUT_SECONDS) -> bool`.

- [ ] **Step 1: Écrire les tests**

```python
# ── _trigger_native_update_dialog : PackageManagerDialog.trigger("SHOW_UPDATE_DIALOG") ──
# L'appel exact de la bulle de notification de LibreOffice (updatecheck.cxx →
# showExtensionDialog). Il met la vérification en file sur le thread de commandes
# de LO et rend la main ; il doit partir du thread principal (AsyncCallback).

PMD = "com.sun.star.deployment.ui.PackageManagerDialog"
ASYNC = "com.sun.star.awt.AsyncCallback"


def _job_with_services(run_callback=True):
    """AsyncCallback synchrone (le main thread est disponible tout de suite)
    ou inerte (run_callback=False) ; PackageManagerDialog mocké."""
    job = _job()
    smgr = job.ctx.getServiceManager.return_value
    default = smgr.createInstanceWithContext.return_value
    dialog = MagicMock(name="PackageManagerDialog")
    async_cb = MagicMock(name="AsyncCallback")
    if run_callback:
        async_cb.addCallback.side_effect = lambda cb, data: cb.notify(data)
    services = {PMD: dialog, ASYNC: async_cb}
    smgr.createInstanceWithContext.side_effect = lambda name, ctx: services.get(name, default)
    return job, dialog, async_cb


def test_trigger_opens_update_dialog_on_main_thread():
    job, dialog, async_cb = _job_with_services()
    assert job._trigger_native_update_dialog(timeout=2) is True
    async_cb.addCallback.assert_called_once()
    dialog.trigger.assert_called_once_with("SHOW_UPDATE_DIALOG")


def test_trigger_returns_false_when_dialog_raises():
    job, dialog, _ = _job_with_services()
    dialog.trigger.side_effect = RuntimeError("Cannot initialize VCL")
    assert job._trigger_native_update_dialog(timeout=2) is False


def test_trigger_times_out_when_main_thread_unavailable():
    job, dialog, _ = _job_with_services(run_callback=False)
    assert job._trigger_native_update_dialog(timeout=0.2) is False
    dialog.trigger.assert_not_called()
```

- [ ] **Step 2: Vérifier l'échec**

Run: `python3 -m pytest tests/unit/test_native_route.py -q -k trigger 2>&1 | tail -3`
Expected: 3 failed, `AttributeError`.

- [ ] **Step 3: Implémenter**

Après `_native_feed_offers` :

```python
    def _trigger_native_update_dialog(self, timeout=_NATIVE_TRIGGER_TIMEOUT_SECONDS):
        """Ouvre, sur le thread PRINCIPAL, le dialogue natif « Mise à jour des
        extensions » : PackageManagerDialog.trigger("SHOW_UPDATE_DIALOG"), l'appel
        exact de la bulle de notification de LibreOffice (updatecheck.cxx). LO
        interroge le feed, télécharge et installe lui-même ; ses dialogues tournent
        sur son thread de commandes, l'appel rend la main aussitôt.
        Vrai si le déclenchement s'est exécuté sans exception avant `timeout`."""
        if _MainThreadCallback is None:
            return False
        holder = {"ok": False, "err": ""}
        done = threading.Event()
        ctx = self.ctx

        def _do_trigger():
            try:
                dialog = ctx.getServiceManager().createInstanceWithContext(
                    "com.sun.star.deployment.ui.PackageManagerDialog", ctx)
                if dialog is None:
                    holder["err"] = "PackageManagerDialog unavailable"
                    return
                dialog.trigger("SHOW_UPDATE_DIALOG")
                holder["ok"] = True
            except Exception as exc:
                holder["err"] = str(exc)
            finally:
                done.set()

        try:
            async_cb = self.ctx.getServiceManager().createInstanceWithContext(
                "com.sun.star.awt.AsyncCallback", self.ctx)
            if async_cb is None:
                return False
            async_cb.addCallback(_MainThreadCallback(_do_trigger), None)
        except Exception as exc:
            log_to_file(f"_trigger_native_update_dialog: schedule failed: {exc}")
            return False
        if not done.wait(timeout):
            log_to_file("_trigger_native_update_dialog: timeout waiting for main thread")
            return False
        if holder["ok"]:
            log_to_file("_trigger_native_update_dialog: SHOW_UPDATE_DIALOG triggered")
            return True
        log_to_file(f"_trigger_native_update_dialog: failed: {holder['err']}")
        return False
```

- [ ] **Step 4: Lancer les tests et committer**

Run: `python3 -m pytest tests/unit/test_native_route.py -q 2>&1 | tail -3`
Expected: vert.

```bash
git add src/mirai/entrypoint.py tests/unit/test_native_route.py
git commit -m "feat(update): déclencher le dialogue natif de MAJ sur le thread principal (#9, #5)

PackageManagerDialog.trigger(\"SHOW_UPDATE_DIALOG\") : le chemin de la bulle
de notification de LibreOffice, vérifié dans dp_gui_service.cxx.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Flux — `_perform_native_update`, choix de route, attente factorisée, cooldown sur « Non »

**Files:**
- Modify: `src/mirai/entrypoint.py` — `_perform_update` (vers 1915-2290) ; nouvelles méthodes `_wait_before_prompting` et `_perform_native_update` après `_trigger_native_update_dialog`.
- Test: `tests/unit/test_native_route.py`

**Interfaces:**
- Consumes: `_native_feed_offers`, `_trigger_native_update_dialog`, `_load_update_state`, `_save_update_state(route=, postponed_until=, native_attempts=)`, `_close_after_inprocess_update`, `_report_update_status`, `_send_telemetry`, `_get_extension_version`.
- Produces: `MainJob._wait_before_prompting()`, `MainJob._perform_native_update(directive, wait_seconds=None, poll_seconds=None) -> bool`.

- [ ] **Step 1: Écrire les tests du flux natif**

```python
# ── _perform_native_update : dialogue natif, surveillance, fermeture ─────

def _native_job(versions, trigger=True):
    """versions : réponses successives de _get_extension_version (la première
    est version_before, puis la surveillance)."""
    job = _job()
    seq = list(versions)
    job._get_extension_version = MagicMock(side_effect=lambda: seq.pop(0) if len(seq) > 1 else seq[0])
    job._wait_before_prompting = MagicMock()
    job._trigger_native_update_dialog = MagicMock(return_value=trigger)
    job._close_after_inprocess_update = MagicMock()
    return job


DIRECTIVE = {"action": "update", "target_version": TARGET, "campaign_id": 7,
             "artifact_url": "/catalog/mirai-libreoffice/download", "urgency": "normal"}


def _events(job):
    return [c.args[0] for c in job._send_telemetry.call_args_list]


def test_native_update_installed_then_closes():
    job = _native_job([CURRENT, CURRENT, TARGET])
    assert job._perform_native_update(DIRECTIVE, wait_seconds=2, poll_seconds=0.01) is True

    job._wait_before_prompting.assert_called_once()
    job._trigger_native_update_dialog.assert_called_once()
    job._report_update_status.assert_called_once_with(7, "deferred", CURRENT, TARGET)
    job._close_after_inprocess_update.assert_called_once()
    state = _state(job)
    assert state["stage"] == "installed_native"
    assert state["route"] == "native"
    assert state["native_attempts"] == 1
    assert state["version_before"] == CURRENT
    assert _events(job) == ["UpdateNativeDialogShown", "UpdateInstalledPendingRestart"]
    attrs = job._send_telemetry.call_args_list[1].args[1]
    assert attrs["route"] == "native" and attrs["version_after"] == TARGET


def test_native_update_postponed_when_nothing_installed():
    job = _native_job([CURRENT])
    before = time.time()
    assert job._perform_native_update(DIRECTIVE, wait_seconds=0.05, poll_seconds=0.01) is True

    job._close_after_inprocess_update.assert_not_called()
    state = _state(job)
    assert state["stage"] == "postponed"
    assert state["route"] == "native"
    assert state["postponed_until"] >= before + entrypoint._UPDATE_POSTPONE_SECONDS - 1
    assert _events(job) == ["UpdateNativeDialogShown", "UpdatePostponed"]
    assert job._send_telemetry.call_args_list[1].args[1]["route"] == "native"


def test_native_update_counts_attempts_for_same_target():
    job = _native_job([CURRENT])
    job._save_update_state(DIRECTIVE, "postponed", route="native", native_attempts=1)
    job._perform_native_update(DIRECTIVE, wait_seconds=0.05, poll_seconds=0.01)
    assert _state(job)["native_attempts"] == 2
    attrs = job._send_telemetry.call_args_list[0].args[1]
    assert attrs["attempt"] == "2"


def test_native_update_returns_false_without_side_effects_when_trigger_fails():
    job = _native_job([CURRENT], trigger=False)
    assert job._perform_native_update(DIRECTIVE, wait_seconds=0.05, poll_seconds=0.01) is False
    job._report_update_status.assert_not_called()
    job._send_telemetry.assert_not_called()
    assert not os.path.isfile(job._update_state_path())
```

- [ ] **Step 2: Écrire les tests du choix de route dans `_perform_update`**

```python
# ── Choix de route dans _perform_update ──────────────────────────────────

def _routing_job(feed_offers=True, native_result=True):
    job = _job()
    job._native_feed_offers = MagicMock(return_value=feed_offers)
    job._perform_native_update = MagicMock(return_value=native_result)
    job._wait_before_prompting = MagicMock()
    job.get_ssl_context = MagicMock()
    job._urlopen = MagicMock()
    job._urlopen.return_value.__enter__.return_value.read.return_value = b"oxt-bytes"
    job._failover_ordered_urls = MagicMock(return_value=["https://dm.example"])
    return job


def test_perform_update_takes_native_route_without_downloading():
    job = _routing_job()
    job._perform_update(dict(DIRECTIVE))
    job._native_feed_offers.assert_called_once_with(TARGET)
    job._perform_native_update.assert_called_once()
    job._urlopen.assert_not_called()


def test_perform_update_falls_back_to_directed_route_when_native_fails():
    job = _routing_job(native_result=False)
    job._perform_update(dict(DIRECTIVE))
    job._perform_native_update.assert_called_once()
    assert job._urlopen.called, "la route dirigée doit télécharger"


def test_perform_update_skips_native_when_feed_diverges():
    job = _routing_job(feed_offers=False)
    job._perform_update(dict(DIRECTIVE))
    job._perform_native_update.assert_not_called()
    assert job._urlopen.called


def test_perform_update_skips_native_for_deferred_urgency_and_rollback():
    job = _routing_job()
    job._perform_update(dict(DIRECTIVE, urgency="deferred"))
    job._perform_update(dict(DIRECTIVE, action="rollback"))
    job._native_feed_offers.assert_not_called()
    job._perform_native_update.assert_not_called()


def test_perform_update_skips_native_after_max_attempts():
    job = _routing_job()
    job._save_update_state(DIRECTIVE, "postponed", route="native",
                           native_attempts=entrypoint._NATIVE_MAX_ATTEMPTS)
    job._perform_update(dict(DIRECTIVE))
    job._native_feed_offers.assert_not_called()
    assert job._urlopen.called


def test_directed_route_postponed_sets_cooldown():
    """Route dirigée, l'utilisateur répond « Non » (msgbox mocké ≠ 2) → état
    postponed avec échéance, route « directed »."""
    job = _routing_job(feed_offers=False)
    before = time.time()
    job._perform_update(dict(DIRECTIVE))
    state = _state(job)
    assert state["stage"] == "postponed"
    assert state["route"] == "directed"
    assert state["postponed_until"] >= before + entrypoint._UPDATE_POSTPONE_SECONDS - 1
    assert "UpdatePostponed" in _events(job)
```

- [ ] **Step 3: Vérifier l'échec**

Run: `python3 -m pytest tests/unit/test_native_route.py -q 2>&1 | tail -5`
Expected: les 10 nouveaux tests échouent (`AttributeError: _perform_native_update` ; `_perform_native_update.assert_called_once()` non appelé).

- [ ] **Step 4: Ajouter `_wait_before_prompting` et `_perform_native_update`**

Après `_trigger_native_update_dialog` :

```python
    def _wait_before_prompting(self):
        """Laisse l'assistant d'enrôlement se terminer (120 s max), puis un délai
        de grâce de 30 s pour ne pas interrompre l'utilisateur d'emblée."""
        _wait_start = time.time()
        _max_wait = 120
        while time.time() - _wait_start < _max_wait:
            with MainJob._enrollment_wizard_lock_cls:
                if not MainJob._enrollment_wizard_active_cls:
                    break
            time.sleep(1)
        time.sleep(30)

    def _perform_native_update(self, directive, wait_seconds=None, poll_seconds=None):
        """Route native pilotée : le DM a décidé (directive), LibreOffice installe.

        Renvoie True si le dialogue a été montré et l'issue traitée — installée
        (fermeture propre) ou reportée (cooldown) ; False si le déclenchement a
        échoué, sans rien rapporter ni persister : l'appelant bascule en route
        dirigée. Après l'installation, l'ancien dossier de l'extension a été
        remplacé : d'ici la fermeture, aucun import de module du plugin.
        """
        wait_seconds = _NATIVE_INSTALL_WAIT_SECONDS if wait_seconds is None else wait_seconds
        poll_seconds = _NATIVE_POLL_SECONDS if poll_seconds is None else poll_seconds
        target_version = str(directive.get("target_version", "")).strip()
        campaign_id = directive.get("campaign_id")
        campaign_attr = str(campaign_id) if campaign_id is not None else ""
        version_before = str(self._get_extension_version() or "")
        previous = self._load_update_state()
        attempts = 0
        if str(previous.get("target_version", "")) == target_version:
            attempts = int(previous.get("native_attempts") or 0)

        self._wait_before_prompting()
        log_to_file(f"_perform_native_update: opening native update dialog for {target_version}")
        if not self._trigger_native_update_dialog():
            return False

        self._report_update_status(campaign_id, "deferred", version_before, target_version)
        self._save_update_state(directive, "native_dialog", route="native",
                                native_attempts=attempts + 1)
        self._send_telemetry("UpdateNativeDialogShown", {
            "version_after": target_version,
            "campaign_id": campaign_attr,
            "route": "native",
            "attempt": str(attempts + 1),
        })

        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            time.sleep(poll_seconds)
            if str(self._get_extension_version() or "") == target_version:
                log_to_file(f"_perform_native_update: {target_version} installed natively, closing for restart")
                self._save_update_state(directive, "installed_native", route="native")
                self._send_telemetry("UpdateInstalledPendingRestart", {
                    "version_after": target_version,
                    "campaign_id": campaign_attr,
                    "route": "native",
                })
                self._close_after_inprocess_update()
                return True

        log_to_file("_perform_native_update: no install detected, postponed")
        self._save_update_state(directive, "postponed", route="native",
                                postponed_until=time.time() + _UPDATE_POSTPONE_SECONDS)
        self._send_telemetry("UpdatePostponed", {
            "version_after": target_version,
            "campaign_id": campaign_attr,
            "route": "native",
        })
        return True
```

- [ ] **Step 5: Brancher le choix de route au début du `try` de `_perform_update`**

Dans `_perform_update`, remplacer :

```python
        tmp_path = None
        try:
            # Download with failover across bootstrap DMs (2 passes), per-URL TLS.
            binary = None
```

par :

```python
        tmp_path = None
        try:
            # Route native d'abord (spec 2026-09-22) : jamais pour une directive
            # différée (elle ne dérange pas l'utilisateur) ni un rollback (LO ne
            # propose que des versions plus récentes) ; bornée en tentatives.
            if action == "update" and urgency != "deferred":
                previous = self._load_update_state()
                attempts = 0
                if str(previous.get("target_version", "")) == str(target_version):
                    attempts = int(previous.get("native_attempts") or 0)
                if attempts < _NATIVE_MAX_ATTEMPTS and self._native_feed_offers(target_version):
                    if self._perform_native_update(directive):
                        return
                    log_to_file("_perform_update: native route unavailable, falling back to directed route")

            # Download with failover across bootstrap DMs (2 passes), per-URL TLS.
            binary = None
```

- [ ] **Step 6: Factoriser l'attente et marquer la route dirigée**

Dans `_perform_update`, remplacer le bloc :

```python
            # Wait for enrollment wizard to finish and let user settle in
            _wait_start = time.time()
            _max_wait = 120  # max 2 min
            while time.time() - _wait_start < _max_wait:
                with MainJob._enrollment_wizard_lock_cls:
                    if not MainJob._enrollment_wizard_active_cls:
                        break
                time.sleep(1)
            # Extra grace period so the user isn't interrupted immediately
            time.sleep(30)
            log_to_file("_perform_update: showing update dialog to user")
```

par :

```python
            self._wait_before_prompting()
            log_to_file("_perform_update: showing update dialog to user")
```

Puis, dans la branche `if user_wants_restart:`, remplacer :

```python
                self._save_update_state(directive, "user_accepted")
                self._send_telemetry("UpdateAccepted", {
                    "version_after": target_version,
                    "campaign_id": str(campaign_id) if campaign_id is not None else "",
                    "urgency": urgency,
                })
```

par :

```python
                self._save_update_state(directive, "user_accepted", route="directed")
                self._send_telemetry("UpdateAccepted", {
                    "version_after": target_version,
                    "campaign_id": str(campaign_id) if campaign_id is not None else "",
                    "urgency": urgency,
                    "route": "directed",
                })
```

Dans la même branche, remplacer :

```python
                    self._send_telemetry("UpdateInstalledPendingRestart", {
                        "version_after": target_version,
                        "campaign_id": str(campaign_id) if campaign_id is not None else "",
                    })
```

par :

```python
                    self._send_telemetry("UpdateInstalledPendingRestart", {
                        "version_after": target_version,
                        "campaign_id": str(campaign_id) if campaign_id is not None else "",
                        "route": "directed",
                    })
```

et :

```python
                self._send_telemetry("UpdateInstallFailed", {
                    "version_after": target_version,
                    "campaign_id": str(campaign_id) if campaign_id is not None else "",
                    "fallback": "manual",
                })
```

par :

```python
                self._send_telemetry("UpdateInstallFailed", {
                    "version_after": target_version,
                    "campaign_id": str(campaign_id) if campaign_id is not None else "",
                    "fallback": "manual",
                    "route": "directed",
                })
```

Enfin, remplacer la branche `else:` (« user postponed restart ») par :

```python
            else:
                log_to_file("_perform_update: user postponed restart")
                self._save_update_state(directive, "postponed", route="directed",
                                        postponed_until=time.time() + _UPDATE_POSTPONE_SECONDS)
                self._send_telemetry("UpdatePostponed", {
                    "version_after": target_version,
                    "campaign_id": str(campaign_id) if campaign_id is not None else "",
                    "urgency": urgency,
                    "route": "directed",
                })
```

- [ ] **Step 7: Lancer les tests**

Run: `python3 -m pytest tests/unit/test_native_route.py tests/unit/test_native_update.py tests/unit/test_update_features.py tests/unit/test_update_blocked.py -q 2>&1 | tail -3`
Expected: vert. Si un test existant de `test_update_features.py` pilote `_perform_update` et se met à échouer parce que `ctx.getValueByName` renvoie un `MagicMock` itérable vide, `_native_feed_offers` renvoie faux et la route dirigée s'exécute comme avant : vérifier le message d'erreur avant de toucher au test.

- [ ] **Step 8: Commit**

```bash
git add src/mirai/entrypoint.py tests/unit/test_native_route.py
git commit -m "feat(update): route native pilotée par le DM, route dirigée en repli, cooldown sur refus (#9, #5)

Sur directive, si le feed installé annonce la cible, le plugin ouvre le
dialogue natif de LibreOffice qui télécharge et installe lui-même ; le
plugin surveille la version, puis ferme proprement. Sinon, route dirigée
de fix/MAJ. Un refus (natif ou dirigé) n'est pas reproposé avant 24 h.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Télémétrie — événements techniques, action du nouvel événement, `route` à la réconciliation

**Files:**
- Modify: `src/mirai/entrypoint.py` — `_ACTION_NAMES` (vers la ligne 1005), `_TECHNICAL_EVENTS` (vers la ligne 1048), `_reconcile_update_state` (télémétrie `ExtensionUpdated`).
- Test: `tests/unit/test_native_route.py`

- [ ] **Step 1: Écrire les tests**

```python
# ── Télémétrie ───────────────────────────────────────────────────────────

UPDATE_EVENTS = {
    "UpdateStaged", "UpdateAccepted", "UpdatePostponed", "UpdateInstalledPendingRestart",
    "UpdateInstallFailed", "UpdateNativeDialogShown", "ExtensionUpdated", "NativeFeedCheck",
}


def test_update_events_are_technical():
    """Sans cela, le pipeline sécurisé jette ces spans avant la liaison
    utilisateur : l'entonnoir de campagne serait aveugle sur une partie du parc."""
    assert UPDATE_EVENTS <= MainJob._TECHNICAL_EVENTS


def test_action_names_cover_native_dialog_event():
    assert MainJob._ACTION_NAMES["UpdateNativeDialogShown"] == "update"


def test_reconcile_joins_route_to_extension_updated():
    job = _job(current_version=TARGET)
    job._save_update_state(DIRECTIVE, "installed_native", route="native")
    job._reconcile_update_state()
    updated = [c for c in job._send_telemetry.call_args_list if c.args[0] == "ExtensionUpdated"]
    assert len(updated) == 1
    assert updated[0].args[1]["route"] == "native"
    assert updated[0].args[1]["confirmed"] == "true"
```

- [ ] **Step 2: Vérifier l'échec**

Run: `python3 -m pytest tests/unit/test_native_route.py -q -k "technical or action_names or reconcile_joins" 2>&1 | tail -3`
Expected: 3 failed.

- [ ] **Step 3: Implémenter**

Dans `_ACTION_NAMES`, après `"NativeFeedCheck": "update",` ajouter :

```python
        "UpdateNativeDialogShown": "update",
```

Dans `_TECHNICAL_EVENTS`, après `"ActionUnhandled",` ajouter :

```python
        # Flux de mise à jour (issue #9) : télémétrie technique de flotte,
        # envoyée même avant la liaison utilisateur.
        "UpdateStaged",
        "UpdateAccepted",
        "UpdatePostponed",
        "UpdateInstalledPendingRestart",
        "UpdateInstallFailed",
        "UpdateNativeDialogShown",
        "ExtensionUpdated",
        "NativeFeedCheck",
```

Dans `_reconcile_update_state`, remplacer :

```python
                self._send_telemetry("ExtensionUpdated", {
                    "version_after": target,
                    "campaign_id": str(state.get("campaign_id") or ""),
                    "confirmed": "true",
                })
```

par :

```python
                self._send_telemetry("ExtensionUpdated", {
                    "version_after": target,
                    "campaign_id": str(state.get("campaign_id") or ""),
                    "confirmed": "true",
                    "route": str(state.get("route") or ""),
                })
```

- [ ] **Step 4: Lancer la suite ciblée puis complète**

Run: `python3 -m pytest tests/unit/test_native_route.py tests/unit/test_native_update.py tests/unit/test_update_features.py tests/unit/test_update_blocked.py tests/unit/test_pump_events_safety.py -q 2>&1 | tail -3`
Expected: vert.

Run: `python3 -m pytest tests/unit/ tests/integration/ -q 2>&1 | tail -3`
Expected: aucun nouvel échec par rapport à la base notée en Task 0.

- [ ] **Step 5: Commit**

```bash
git add src/mirai/entrypoint.py tests/unit/test_native_route.py
git commit -m "feat(telemetry): flux de MAJ classé technique, route jointe à ExtensionUpdated (#9)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Documentation

**Files:**
- Modify: `docs/update-natif-libreoffice.md` (section « Vue d'ensemble — trois routes » et paragraphe « Point établi »)
- Modify: `README.md` (section « Mise à jour automatique côté plugin », étape 5)

- [ ] **Step 1: Corriger `docs/update-natif-libreoffice.md`**

Remplacer le tableau des trois routes et le paragraphe « Point établi en vérifiant les sources LibreOffice » par :

```markdown
| Route | Déclencheur | Téléchargement + installation | Cohorte/canary |
|---|---|---|---|
| **1. Native pilotée** (principale) | Directive `update` du DM, si le feed installé annonce exactement `target_version` | LibreOffice : dialogue « Mise à jour des extensions » ouvert par le plugin via `PackageManagerDialog.trigger("SHOW_UPDATE_DIALOG")`, download depuis le feed, `addExtension` | ✅ sur le déclenchement (le feed, lui, est anonyme) |
| **2. Dirigée** (repli) | Directive `update` ou `rollback` ; feed absent, injoignable ou divergent ; urgence `deferred` ; deux tentatives natives sans installation | Plugin : download failover + sha256 → `addExtension` main-thread → fermeture propre | ✅ oui |
| **3. Manuelle** (fallback validé GPO) | Échec de 2 | L'utilisateur double-clique l'OXT stagé → Gestionnaire des extensions | ✅ (directive) |

Point établi en vérifiant les sources LibreOffice (`desktop/source/deployment/gui/`) :
le service créable `com.sun.star.deployment.ui.PackageManagerDialog` implémente
`XJobExecutor`, et `trigger("SHOW_UPDATE_DIALOG")` ouvre directement le dialogue de
mise à jour des extensions — le chemin de la bulle de notification de LibreOffice
(`updatecheck.cxx`, `showExtensionDialog`). C'est ainsi que la directive DM produit
l'effet « push » sur la route native. Le bouton « Vérifier les mises à jour » du
Gestionnaire des extensions reste disponible au support, indépendamment du plugin.

**Refus et reports.** Un « Non » (route 2), une annulation ou une version ignorée dans
le dialogue natif (route 1) posent `postponed_until` dans `pending_update/update_state.json` :
la cible n'est pas reproposée avant 24 h, et rien n'est retéléchargé entre-temps.
```

- [ ] **Step 2: Mettre à jour le README**

Dans « Mise à jour automatique côté plugin », remplacer l'étape 5 (« Installe la mise à jour, avec repli en cascade ») par :

```markdown
5. **Installe la mise à jour**, avec repli en cascade — un seul primitif, `addExtension` sur le thread principal :
   1. **Route native pilotée** — si le feed `<update-information>` de l'extension installée annonce exactement la version cible, le plugin ouvre le dialogue natif « Mise à jour des extensions » (`PackageManagerDialog`, `SHOW_UPDATE_DIALOG`) : LibreOffice télécharge et installe lui-même, avec sa pile HTTP ; le plugin ferme ensuite LibreOffice proprement. _(#5, #9)_
   2. **Route dirigée** — sinon (amorçage, rollback, feed injoignable ou divergent) : téléchargement avec failover multi-bootstrap, sha256, `addExtension` in-process, fermeture propre. **Aucun processus enfant.** _(#4, #15, #16)_
   3. Sinon, **boîte « mise à jour bloquée »** : mode opératoire manuel + bouton **« Ouvrir le dossier »** (natif, `SystemShellExecute`). _(#7, #12)_
   Un refus n'est pas reproposé avant 24 h.
```

- [ ] **Step 3: Commit**

```bash
git add docs/update-natif-libreoffice.md README.md
git commit -m "docs(update): route native pilotée, ordre des routes, cooldown (#9, #5)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: Build et vérification finale

**Files:**
- Aucun.

- [ ] **Step 1: Build OXT avec injection du feed**

Run: `./scripts/02-build-oxt.sh --config config/profiles/config.default.dev.json 2>&1 | tail -5`
Expected: build OK, ligne `update-information baked (N feed URL(s))`.

- [ ] **Step 2: Vérifier le bloc dans le staging ou l'OXT produit**

Run: `unzip -p dist/*.oxt description.xml | grep -A3 update-information`
Expected: un `<src xlink:href="…/catalog/mirai-libreoffice/update.xml"/>` par `bootstrap_urls` du profil.

- [ ] **Step 3: Suite complète et journal**

Run: `python3 -m pytest tests/unit/ tests/integration/ -q 2>&1 | tail -3 && git log --oneline origin/fix/MAJ..HEAD`
Expected: aucun nouvel échec ; 8 commits (spec, état, cooldown, feed, trigger, flux, télémétrie, docs).

---

## Validation terrain (hors plan, avant fusion large) — spec §7

Macro Basic sur un poste durci disposant d'une version qui embarque le bloc feed, feed
d'intégration servi par le DM (plan DM Task 1) :

```basic
Sub TestMirAIUpdateDialog
    Dim oDlg As Object
    oDlg = createUnoService("com.sun.star.deployment.ui.PackageManagerDialog")
    oDlg.trigger("SHOW_UPDATE_DIALOG")
End Sub
```

Trois cycles consécutifs, contrôle de `registrymodifications.xcu`, télémétrie
`NativeFeedCheck` positive, journal AppLocker en cas de refus.
