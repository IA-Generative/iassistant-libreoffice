# CLAUDE.md : MIrAI LibreOffice

Extension LibreOffice (OXT) qui met un assistant IA dans Writer et Calc. Python pur
sur l'API UNO, exécuté par le Python embarqué de LibreOffice (3.11) : bibliothèque
standard et UNO seulement, aucun paquet pip. Backend OpenAI-compatible, configuration,
enrôlement et mises à jour viennent du Device Management (dépôt voisin
`../device-management/`).

Documents, commentaires et libellés sont en français ; identifiants, branches et
commits en anglais. Format des commits, branches et PR : CONTRIBUTING.md.

## Commandes

```bash
./scripts/03-test-local.sh                           # lint + tests + build, ce que la CI exécute
python3 -m pytest tests/unit tests/integration -q    # tests seuls (pytest suffit)
./scripts/02-build-oxt.sh --config config/profiles/config.default.dev.json --install
```

Toujours un `--config` au build : sans lui, l'OXT embarque le `config/config.default.json`
gitignoré du poste. `dev-launch.sh` et `00-clean-install.sh` sont macOS seulement.
Déploiement et campagnes : docs/DEPLOY.md ; toute release part en `immediate` tant que
device-management#49 n'est pas livré.

## Carte

docs/ARCHITECTURE.md est la carte (couches, tools, règles, « ajouter un tool en 5
étapes ») ; la refacto de la coquille suit docs/adr/0001-refacto-strangler.md.

- `src/mirai/entrypoint.py` : la coquille (`MainJob`) : enrôlement, SSO, Device
  Management, mise à jour, télémétrie, dispatch des actions.
- `src/mirai/shell/` : modules extraits de la coquille ; `runtime.py` porte l'état du
  processus.
- `src/mirai/core/` : le moteur : registry de tools UNO, orchestrateur, client LLM ;
  `shell_facade.py` est le seul pont vers `MainJob`, duck-typé.
- `src/mirai/ui/` : la palette universelle (DSFR).
- `src/mirai/formatting/`, `security_flow.py` : legacy, suppression décidée
  (ADR 0001).
- `oxt/` : description, menu `🤖 MIrAI`, raccourci, jobs ; `config/profiles/` : les
  profils embarqués au build.
- `tests/unit/rules/` : les règles d'architecture, exécutables ; `tests/stubs/` : les
  faux UNO, dont `install()` précède tout import de `src.mirai` (d'où E402 toléré
  dans `tests/`).

## Règles

Chacune existe parce que la violer plante LibreOffice, fait fuir une donnée ou rend
le moteur intestable. `tests/unit/rules/` et `tests/unit/core/test_formula_guard.py`
les font respecter.

1. **Tout accès UNO se fait sur le thread principal.** Le run vit dans un thread
   worker ; document, contrôles, undo et tools y repassent par
   `MainThreadDispatcher.call` (lire ou modifier) ou `.post` (afficher), dans
   `core/ui_thread.py`. Seule `entrypoint.pump_events` pompe les événements, et
   seulement sur le thread principal : pomper depuis un autre thread tue
   LibreOffice (abort dans `DispatchUserEvents`). Un module qui a besoin d'UNO
   l'importe en tête de fichier : un import UNO paresseux échoue hors du thread
   principal.
2. **`core/` et `ui/` atteignent la coquille par `core/shell_facade.py`**, jamais
   par un import d'`entrypoint` : c'est ce qui permet de tester le moteur sans
   LibreOffice.
3. **Une cellule Calc s'écrit par `calc_tools.safe_set_string`** ou après
   `calc_functions.formula_refusal` : aucune formule produite par l'assistant
   n'atteint le réseau ni un fichier.
4. **La télémétrie ne porte jamais de contenu documentaire** : compteurs, statuts,
   durées et noms de tools seulement.
5. **Bibliothèque standard et UNO seulement** : `urllib.request` pour le réseau,
   validateur JSON maison dans `core/tool_calls.py`, dialogues `com.sun.star.awt`.

## Base de code saine

Le code neuf va là où il se lit seul : un module ou une classe par responsabilité,
des dépendances reçues explicitement plutôt que lues sur `self`, des fonctions qui
tiennent sur un écran (ruff mesure la complexité, cible : aucune fonction au-delà
de 10). `entrypoint.py` est le contre-exemple, pas le modèle : il se vide par
extraction (docs/adr/0001-refacto-strangler.md), et ce qu'une PR y ajouterait va
dans `shell/` : l'état qui survit d'une action à l'autre dans `ShellRuntime`, une
tâche de fond lancée par `__init__` dans `MainJob.STARTUP_TASKS`.

`tests/unit/rules/test_shell_budget.py` et `test_test_surface.py` mesurent cette
extraction. Quand une borne bloque, deux issues : sortir ce que la PR ajoute dans
son propre module, ou relever la borne et le dire dans la PR. Compacter le code
pour passer n'en est pas une.

## Tests et commentaires

Un test décrit ce que le code fait pour un utilisateur ou un appelant. Il ne décrit
pas la modification qui vient d'être faite : son nom et sa docstring restent vrais
quand plus personne ne se souvient du ticket.

Mauvais :

```python
def test_notify_update_blocked_uses_querybox(tmp_path):
    """Régression : la boîte doit être un QUERYBOX (type 4), un INFOBOX
    n'affichait pas « Oui » (bug observé sur la 0.0.1.0.18)."""
```

Bon :

```python
def test_update_blocked_with_a_folder_offers_yes_and_no(tmp_path):
    """Quand un dossier est proposé, la boîte pose une question (Oui/Non)."""
```

Un commentaire dit pourquoi le code est ainsi aujourd'hui, jamais ce qu'il était
avant ni quel ticket l'a changé : `git log` s'en charge. Les tests de
`tests/unit/rules/` décrivent une règle, pas une fonctionnalité : c'est leur rôle.

## Pièges

- Profils : le fichier s'appelle `config.default.integration.json`, le profil demandé
  au Device Management s'appelle `int` (noms acceptés : `dev`, `int`, `prod`).
- Raccourci de la palette : Ctrl+Alt+Espace (macOS : Ctrl+Opt+Espace), jamais
  Ctrl+Shift+Espace, qui insère une espace insécable dans Writer et sélectionne
  tout dans Calc.
- macOS : `unopkg` tué par « Launch Constraint Violation » après chaque mise à jour
  de LibreOffice : re-signer ses binaires (docs/ARCHITECTURE.md, § Environnement).
