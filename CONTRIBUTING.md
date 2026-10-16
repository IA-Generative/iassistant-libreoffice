# Contribuer à MIrAI LibreOffice

## Branches et PR

- Les PR visent `develop`. `master` ne reçoit que `develop`, au moment d'une
  release (voir [docs/DEPLOY.md](docs/DEPLOY.md)).
- Une PR fait un déplacement ou une correction, jamais les deux.
- Les PR sont fusionnées par un merge commit.
- `master` étant la branche par défaut, un « Closes #N » dans une PR vers
  `develop` ne ferme pas le ticket : le fermer à la main après la fusion.

## Commits

Sujets en anglais, au format Conventional Commits ; la CI les vérifie sur chaque
PR. `feat`, `fix`, `perf` et `revert` alimentent le `CHANGELOG.md` et les notes
affichées par le Device Management ; `docs`, `refactor`, `test`, `build`, `ci`,
`chore` et `style` n'y figurent pas.

## Avant la PR

```bash
./scripts/03-test-local.sh
```

Le plugin n'a aucune dépendance pip : pytest et ruff suffisent. `PYTHON` choisit
l'interpréteur, par exemple celui d'un venv.

## Traductions

Les libellés vivent dans `src/mirai/locales/<langue>.json`, un fichier par
langue (fr, en, es, pt, zh). Une clé ajoutée l'est dans les cinq fichiers, une
seule fois chacun : `tests/unit/test_i18n.py` vérifie la parité, les doublons,
les clés écrites en toutes lettres dans un appel `_t("…")` et les libellés de
menu d'`oxt/Addons.xcu`, qui restent en XML.

## Base de code saine

Le code neuf va là où il se lit seul : un module ou une classe par responsabilité,
des dépendances reçues explicitement plutôt que lues sur `self`, des fonctions qui
tiennent sur un écran (`03-test-local.sh` compte les fonctions de `core/` et `ui/`
dont la complexité dépasse 10 ; la cible est zéro).

`src/mirai/entrypoint.py` est le contre-exemple, pas le modèle : il se vide par
extraction, selon [docs/adr/0001-refacto-strangler.md](docs/adr/0001-refacto-strangler.md).
Ce qu'une PR y ajouterait va dans `src/mirai/shell/` : l'état qui survit d'une
action à l'autre dans `shell/runtime.py` (`ShellRuntime`), une tâche de fond lancée
par `MainJob.__init__` dans `MainJob.STARTUP_TASKS`.

`tests/unit/rules/test_shell_budget.py` et `test_test_surface.py` mesurent cette
extraction : taille de la coquille, état de classe, threads, membres privés de
`MainJob` cités par les tests. Quand une borne bloque une PR, deux issues : sortir
ce que la PR ajoute dans son propre module, ou relever la borne et le dire dans la
PR. Compacter le code pour passer n'en est pas une.

Les règles d'architecture (thread principal, façade, formules Calc, télémétrie)
sont dans [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), chacune doublée d'un test.
