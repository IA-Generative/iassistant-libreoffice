# ADR-0001 : Refacto progressive de la coquille (strangler)

**Date** : 2026-10-08
**Statut** : Accepté
**Portée** : `src/mirai/entrypoint.py` (la coquille, `MainJob`) et les modules qui
en sortiront ; règles des PR pendant la refacto. Le moteur (`core/`) et la palette
(`ui/`) gardent leur forme.

---

## Contexte

`entrypoint.py` compte 10 425 lignes et `MainJob` 166 méthodes : enrôlement, SSO
Keycloak, Device Management, mise à jour, télémétrie, transport HTTP, dialogues.
`MainJob` est réinstancié à chaque action, mais garde l'état du processus dans
18 attributs de classe. Les tests instancient le vrai `MainJob` et citent ses
membres privés. Chaque correction passe par ce fichier, qui a grossi au lieu de
maigrir : 10 255 lignes le 25/09, 11 199 le 06/10 avant nettoyage.

Une réécriture ou un découpage en mixins garderait le couplage par `self` et l'état
de classe. Une réécriture risque aussi de bloquer le parc : une release qui casse
l'updater ne peut plus être corrigée par l'updater.

## Décision

La coquille est extraite progressivement, derrière la façade existante
(`core/shell_facade.py`), sans réécriture ni mixins.

1. **La surface UNO ne bouge pas** : `main.py`, le nom d'implémentation
   `fr.gouv.interieur.mirai.do`, `MainJob.trigger` et `execute`, `Jobs.xcu`,
   `Addons.xcu`, `manifest.xml`. `MainJob` reste la classe enregistrée ; elle
   maigrit sans changer d'identité.
2. **Un service est un module sans UNO**, ou avec UNO injecté (`ctx`, dispatcher),
   construit avec des dépendances explicites. Les services UNO se résolvent sur le
   thread principal et le service reçoit des valeurs.
3. **Déléguer, repointer, supprimer.** Une méthode extraite laisse dans `MainJob` un
   délégué du même nom. Les tests sont ensuite repointés sur le service, puis le
   délégué disparaît : trois PR, jamais une.
4. **Une PR fait un déplacement ou une correction, jamais les deux.** Un déplacement
   est iso-comportement et se relit avec `git diff --color-moved`. Un défaut trouvé
   en chemin se corrige dans sa propre PR, avec un test qui le décrit.
5. **Les tests de caractérisation précèdent l'extraction** et portent sur
   l'observable : fichiers écrits, requêtes émises, spans, messages affichés.
6. **Chaque règle d'architecture est un test**, dans `tests/unit/rules/`, écrit
   avant le premier module qu'elle protège.
7. **L'ordre suit le risque** : supprimer le code mort et fermer la façade d'abord,
   extraire les services purs ensuite, l'updater et l'enrôlement en dernier.

### La coquille ne grossit plus

Une PR fonctionnelle qui touche une zone d'`entrypoint.py` en extrait ce qu'elle
ajoute. `tests/unit/rules/test_shell_budget.py` borne la coquille (lignes, méthodes
de `MainJob`, état de classe, threads, fonctions longues, `except: pass`, imports UNO
dans des fonctions) et `tests/unit/rules/test_test_surface.py` borne les membres
privés cités par les tests. Ces bornes ne font que baisser : une PR qui fait baisser
un indicateur abaisse sa borne, et une PR qui le ferait monter extrait ce qu'elle
ajoute ou justifie la hausse en revue.

### Décisions du 07/10/2026

- **Suppressions** : le menu contextuel Writer (fait), `menu_actions/`,
  `formatting/`, `security_flow.py`, `run_extend`, le dialogue proxy et l'action
  `OpenmiraiWebsite`, le bloc script `unopkg` (`MIRAI_UPDATE_ALLOW_SCRIPT`),
  `_KEYWORD_MAP`, les scripts morts, et le dossier `prompts/` une fois ses
  décisions reprises en ADR. « Corriger » et « Traduire » reviennent comme chips de
  la palette (#81).
- **Rangement** : les modules communs restent à plat dans `src/mirai/`
  (`local_config`, `credentials`, `log_setup`, `feed_rewrite`, `i18n`).
  `ShellRuntime`, propriétaire unique de l'état du processus, vit dans
  `src/mirai/shell/runtime.py`.
- **Releases** : tant que le Device Management n'a pas de garde de version minimale
  du plugin (IA-Generative/device-management#49), toute release part en `immediate`
  pour tout le parc.
- **Langue** : commits et branches en anglais, en Conventional Commits.

## Ce qu'on ne fait pas

- Réécrire la coquille, ou la découper en mixins : les mixins gardent le `self`
  global et l'état de classe, et chaque test instancierait toujours `MainJob`.
- Corriger « au passage » dans une PR de déplacement.
- Importer un type UNO paresseusement hors du thread principal : un module qui en a
  besoin l'importe en tête, et `entrypoint.py` l'importe au chargement.

## Conséquences

- La feuille de route est le ticket #98 ; chaque phase y liste ses tickets.
- Les bornes de `tests/unit/rules/` mesurent l'avancement. La refacto est finie
  quand `entrypoint.py` ne garde que l'enregistrement UNO, la construction du
  runtime et `trigger`/`execute`.
- Les tests de la coquille passent progressivement de `make_job` à des services
  testés sans UNO.
