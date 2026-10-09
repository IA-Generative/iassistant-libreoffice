# Contribuer à MIrAI LibreOffice

## Branches et PR

- Les PR visent `develop`. `master` ne reçoit que `develop`, au moment d'une
  release (voir [docs/DEPLOY.md](docs/DEPLOY.md)).
- Une PR fait un déplacement ou une correction, jamais les deux.
- Les PR sont fusionnées par un merge commit.

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

## La coquille ne grossit plus

La refacto de `src/mirai/entrypoint.py` suit
[docs/adr/0001-refacto-strangler.md](docs/adr/0001-refacto-strangler.md).

- Une PR fonctionnelle qui touche une zone d'`entrypoint.py` en extrait ce
  qu'elle ajoute.
- Les règles de `tests/unit/rules/` bornent la coquille et les membres privés de
  `MainJob` cités par les tests. Ces bornes ne font que baisser.
- `core/` et `ui/` n'importent jamais `entrypoint` : ils passent par la façade
  `core/shell_facade.py`.
- Tout accès UNO se fait sur le thread principal (`core/ui_thread.py`).
