# CLAUDE.md — MIrAI LibreOffice Extension

## Project

Extension LibreOffice (OXT) intégrant un assistant IA dans Writer et Calc. Se connecte à un backend OpenAI-compatible via Device Management.

## Quick commands

```bash
# Build
./scripts/02-build-oxt.sh

# Dev cycle (build + install + launch LO)
./scripts/dev-launch.sh
./scripts/dev-launch.sh --config config/profiles/config.default.integration.json

# Clean install (purge cache + uninstall)
./scripts/00-clean-install.sh --uninstall

# Deploy release (canary rollout)
./scripts/deploy-release.sh \
  --bootstrap-url https://bootstrap.fake-domain.name \
  --strategy canary --profile int

# Deploy release (immediate)
./scripts/deploy-release.sh \
  --bootstrap-url https://bootstrap.fake-domain.name \
  --strategy immediate --profile prod

# Check campaign progress
curl -s -H "X-Admin-Token: $DM_ADMIN_TOKEN" \
  https://bootstrap.fake-domain.name/api/campaigns/{id}/progress | python3 -m json.tool

# Simulate 100 devices
python3 tests/simulation/deploy_simulator.py \
  --devices 100 --bootstrap-url https://bootstrap.fake-domain.name --profile int

# Tests (unitaires + intégration + lint + build)
./scripts/03-test-local.sh
python3 -m pytest tests/unit/ tests/integration/ -q

# K8s deploy (device-management Scaleway)
cd ../device-management && ./scripts/k8s/deploy.sh scaleway
```

## Architecture

> ⚠️ Branche `exp-jetable/demonstrateur-v2` : démonstrateur jetable —
> le cœur est réécrit en moteur MCP interne + palette universelle DSFR.
> Voir **docs/ARCHITECTURE.md** (carte des couches, tools, règles, checklist
> « ajouter un tool »). La coquille (enrollment/SSO/DM/update/télémétrie)
> reste dans entrypoint.py, inchangée.

- `src/mirai/entrypoint.py` — Coquille (MainJob) + dispatch `OpenAssistant`
- `src/mirai/core/` — Moteur : registry de tools UNO, orchestrateur agentique,
  client LLM double-mode (natif/JSON), sinks, presets, conversation, façade
  (`shell_facade.py` — seul pont vers MainJob, duck-typé, jamais d'import)
- `src/mirai/ui/` — Palette universelle (dsfr.py tokens + palette.py)
- `src/mirai/menu_actions/` — legacy, encore présent (suppression prévue après
  validation du démonstrateur)
- `oxt/Addons.xcu` — menu « 🤖 MIrAI » : entrée « 🤖 Ouvrir l'assistant »
  (+ Paramètres, Tester le modèle, Documentation, À propos) et bouton de barre ; raccourci
  Ctrl+Alt+Espace (macOS : Ctrl+Opt+Espace) — jamais Ctrl+Shift+Espace
- `config/profiles/` — Bootstrap config profiles (dev, docker, integration, kubernetes, production)

## Key constraints

- **Threading**: le run vit dans un thread worker ; tout accès UNO (document,
  contrôles, undo, tools) repasse par `MainThreadDispatcher` (`core/ui_thread.py`).
  `processEventsToIdle` est **interdit dans `core/` et `ui/`** — règle testée
  (`test_core_and_ui_never_pump_events`). Dans la coquille legacy, ne jamais
  l'appeler depuis un thread de fond (crash LibreOffice).
- **No pip**: Only `urllib.request` — no external Python packages in the plugin
- **UNO API**: All UI via `com.sun.star.awt.*` dialogs
- **Config profiles**: `dev`, `int`, `prod` (not `integration` — device-management rejects it)
- **core/ui n'importent jamais entrypoint** (règle testée : `tests/unit/core/test_no_entrypoint_import.py`)
- **macOS dev** : si `unopkg` échoue en SIGKILL « Launch Constraint Violation », re-signer ad hoc les binaires auxiliaires de LibreOffice.app (voir docs/ARCHITECTURE.md §Environnement) — à refaire après chaque mise à jour de LO

## Device Management (sibling repo)

Located at `../device-management/`. Key files:
- `app/main.py` — Config endpoint, update directives, enrollment
- `app/admin/` — Admin UI (campaigns, artifacts, cohorts, devices)
- `deploy/k8s/overlays/scaleway/` — Scaleway Kapsule deployment
- K8s DNS resolver: `10.32.0.10` (for relay-assistant nginx)
