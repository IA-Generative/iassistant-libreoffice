# Rapport d'exécution — 2026-07-26

> **Phase B du plan de refonte.** Branche `exp-jetable/demonstrateur-v2`, partie de
> `origin/master` (`621c5c8`) puis fusionnée avec le moteur MCP.
> Document factuel : ce qui a été fait, ce qui ne l'a pas été, et pourquoi.

## En une phrase

La suite de tests est **verte** (556 tests : 550 unitaires + 6 d'intégration), la palette
**s'ouvre en LibreOffice réel**, la chaîne d'authentification `/llm/v1` est vérifiée de bout
en bout **contre les deux tiers** — DM local Ollama et DM Scaleway avec SSO Keycloak réel —
et **10 des 22 constats** de qualification sont corrigés, les trois urgences comprises. Le
reste est listé plus bas, avec sa raison.

## Ce qui a été livré, commit par commit

| Commit | Objet | Suite |
|---|---|---|
| `055b81b` | **Phase A** : qualification de `master` + les deux configurations | 224 (baseline) |
| `fea611b` | Fusion `master` (menu contextuel de Richard) + moteur MCP + palette | 371 ✅ |
| `2cb3912` | **Exécution non bloquante** : worker + `MainThreadDispatcher`, fin du drain | 398 ✅ |
| `16d000c` | **Dispatch** : plus aucun clic sans effet (R-04 → R-07) | 408 ✅ |
| `78b4248` | Tests d'intégration réparés, hôtes internes retirés, doc alignée | 414 ✅ |
| `cb29f24` | Qualité : ruff, `03-test-local.sh` étendu, code mort supprimé | 414 ✅ |
| `882685d` | IHM : statut coloré + indicateur de sélection en direct | 427 ✅ |
| `323efd3` | **Threading** : `pump_events()` — pomper hors thread principal ne peut plus aborter | 433 ✅ |
| `44efa5b` | **IHM (étapes 15→18)** : onglets, suggestions, redimensionnement, menu | 453 ✅ |
| `df3a13d` | **Outils** : `writer_replace_paragraphs` + prompt « AGIS, NE DÉCRIS PAS » | 461 ✅ |
| `49578e4` | **Intention** : détecter les questions, pas les ordres — fin de la liste de verbes | 486 ✅ |
| `4c2badf` | **Capacités** : sonde à trois questions + menu « Tester le modèle » + cache | 512 ✅ |
| `60970d1` | Le raisonnement passe de l'infobulle à une zone persistante et défilable | 522 ✅ |
| `c2d2362` | « Ajouter à la suite » pilote **toutes** les destinations (`presets.text_sink`) | 536 ✅ |
| `b875f10` | Le prompt libre **avec sélection** modifie enfin le document | 545 ✅ |
| `f41d9e8` | Arguments numériques **ramenés dans les bornes** au lieu de faire échouer l'appel | 550 ✅ |

Tags posés : `exp-jetable-v2-baseline`, `exp-jetable-v2-worker`.

## Session de recette du 2026-07-26 — dix-neuf défauts trouvés à l'usage

Aucun n'était visible en test automatisé. Ils se répartissent en trois familles,
et c'est cette répartition qui est instructive pour la suite.

**Famille 1 — le marshalling vers le thread principal (5 défauts, 4 tentatives).**
La cause racine a résisté longtemps : `AsyncCallback` posté depuis un thread de
fond **ne réveille pas** la boucle d'événements de LibreOffice. La tâche attend
le prochain geste de l'utilisateur. D'où des interfaces figées alors que le
journal disait `run: terminé`. Trois correctifs ont visé à côté (service
conservé, instantané préchargé, filet auto-réparant) avant que l'instrumentation
de fin de run ne désigne le vrai mécanisme. La parade — une pompe qui draine une
file et se réarme depuis le thread principal — a elle-même provoqué un gel
immédiat à sa première version, faute de condition d'arrêt.

**Famille 2 — l'écart entre « la donnée est là » et « l'utilisateur la voit »
(4 défauts).** Un catalogue d'outils sans pendant écriture, un onglet
qu'aucun chemin n'alimente, un modèle écrit sans repeindre le contrôle, une
infobulle qui n'affiche que ce que le modèle n'émet pas. À chaque fois le code
« marchait » et l'utilisateur ne voyait rien.

**Famille 3 — la mise en forme du document (2 défauts).** Écrire sur une plage
multi-paragraphes applique le style du premier à tout ; et préserver les styles
ne suffit pas s'il faut aussi exclure les titres de la plage réécrite.

**Famille 4 — « ça ne fait rien », quatre fois, quatre causes différentes (4
défauts).** C'est l'enseignement le plus coûteux de la journée. Une même plainte
utilisateur a recouvert : un verbe absent de la liste de détection, un modèle qui
lit mais n'enchaîne pas l'écriture, un réglage branché sur un seul chemin sur
deux, et un chemin d'exécution qui n'écrivait nulle part dans le document. À
chaque fois, la tentation était de supposer la cause de la fois précédente. À
chaque fois, c'était une autre. La parade n'est pas un correctif mais une
méthode : **mesurer quel chemin a été emprunté avant de chercher pourquoi il a
échoué** — la télémétrie de preset et la présence (ou l'absence) de la trace
« Document réécrit » y suffisent.

**Famille 5 — la tolérance asymétrique d'un validateur (1 défaut).** Un
dépassement de plafond numérique faisait échouer l'appel entier, alors qu'un
dépassement de longueur de chaîne était tronqué en silence, douze lignes plus
haut dans le même fichier.

Les deux derniers relèvent de l'ergonomie pure : ordre du fil, taille de police.

### Détail

Aucun n'était visible en test automatisé : il a fallu se servir du plugin.

| Symptôme | Cause réelle | Correction |
|---|---|---|
| « le plugin est bloqué » | `processEventsToIdle` depuis un thread → `std::terminate` → la boîte de récupération réclame le SolarMutex que le thread mourant détient | garde `pump_events()`, 18 sites, + 6 tests dont un contrôle AST |
| croix de fenêtre inopérante | aucun `XTopWindowListener` — un dialogue non modal émet `windowClosing` et attend qu'on agisse ; Échap n'était branché que sur le champ de prompt | listener de fermeture + Échap sur tous les contrôles |
| bouton figé sur « Arrêter » | le service `AsyncCallback` était recréé à chaque `post()` sans référence gardée, donc collecté avant de délivrer ; aggravé par un mélange écriture directe / postée | service unique conservé, un seul chemin d'écriture |
| polices deux fois trop grosses | 9-10 pt là où le plan visait 7-8 ; le layout mesuré corrige les positions, pas la taille perçue | 6-7 pt |
| interface figée en fin de run | `AsyncCallback` depuis un worker ne réveille pas la boucle d'événements | pompe auto-entretenue armée depuis le thread principal, éteinte dès la file vide |
| gel immédiat (régression) | la pompe se réarmait à vide et monopolisait la boucle | condition d'arrêt liée au travail restant — 0,0 % CPU au repos |
| zone de texte vide à l'écran | `model.Text` porte la donnée mais ne repeint pas un contrôle doté d'un peer | helper `_set_text` : modèle **et** `control.setText()` |
| onglet Actions toujours vide | seul le mode agentique alimentait le journal | tous les chemins y écrivent |
| titre écrasé par le corps | préserver le style de chaque paragraphe ne suffit pas : il faut exclure les titres de la plage | `doc_rewrite.body_range()` |
| configuration corrompue sous écriture concurrente | `set_config` écrivait en place | écriture atomique (tmp + fsync + replace) |
| « restructure le document en 2 paragraphes » sans effet | **le catalogue d'outils était incomplet** : `writer_get_document_map` numérotait des paragraphes que rien ne savait réécrire. Le modèle répondait du texte — `iterations=1`, `ok=true`, document intact | `writer_replace_paragraphs` + consigne « AGIS, NE DÉCRIS PAS » + bascule automatique sur l'onglet qui reçoit la réponse |
| « reduit à 2 paragraphes. reformate en poème » sans effet | la bascule vers le chemin déterministe reposait sur une **liste de verbes** ; ni « réduis » ni « reformate » n'y figuraient. Une liste de ce genre n'est jamais complète | question inversée : on détecte les **questions** (ouverture interrogative), tout le reste est un ordre |
| le document reste intact, sans erreur | le modèle **lit** le document puis répond du texte : il n'enchaîne pas vers l'écriture. Le run se termine en succès | sonde à trois questions (`capabilities.py`), verdict mis en cache par couple (endpoint, modèle), menu « Tester le modèle » |
| raisonnement illisible au survol | l'infobulle s'évanouit au moindre mouvement : ni lecture longue ni défilement | zone persistante et défilable, ouverte/fermée par clic sur (i) |
| « Ajouter à la suite » sans effet | la case n'était branchée que sur le preset « Modifier », retiré des chips deux heures plus tard. Chaque preset construisait sa destination dans son coin | point de passage unique `presets.text_sink` + deux tests d'architecture par introspection |
| prompt libre **avec sélection** sans effet sur le document | le chemin déterministe n'était branché que sur « aucune sélection ⇒ document entier » ; avec sélection, la demande repartait en mode agentique, dont le sink par défaut est la **palette** | `_run_selection_rewrite`, symétrique du chemin document, passant par le même `text_sink` |
| `✗ Lecture du document — paramètre 'max_chars' : maximum 20000` | le validateur **rejetait** un nombre hors bornes, alors qu'il **tronquait** une chaîne trop longue. Le modèle perdait un tour sur un paramètre de confort | valeurs ramenées dans les bornes, arrondi vers l'intérieur ; type invalide et `enum` hors liste restent refusés |

Tous sont capitalisés dans le plan (pièges **n°23 à 49**) avec leur signature
de diagnostic, et la « règle de complétude du catalogue » ouvre désormais la
checklist « ajouter un tool » d'`ARCHITECTURE.md`.

## Le changement structurant : l'exécution non bloquante

C'était la raison technique principale de la refonte, et le défaut le plus grave de la
qualification (T-01 : un appel LLM lancé depuis un thread de fond alors qu'il pompe
`processEventsToIdle` — crash aléatoire de LibreOffice).

**Avant** : le réseau était déjà dans un thread, mais le thread principal s'immobilisait
ensuite dans une boucle de drain qui pompait les événements. C'est ce pompage imbriqué qui
rendait LibreOffice « mou » et qui a causé un SIGABRT (`std::terminate` dans
`DispatchUserEvents`).

**Après** : le run entier vit dans un thread worker ; le thread principal retourne
immédiatement à la boucle d'événements. Tout ce qui touche UNO — document, contrôles,
undo, exécution des outils — repasse par `MainThreadDispatcher.post()/call()`.

Ce qui rend le changement vérifiable plutôt que déclaratif :

- **Un test d'architecture par AST** interdit `processEventsToIdle` dans `core/` et `ui/`.
  Il inspecte l'arbre syntaxique, pas le texte : une docstring qui explique la règle reste
  permise, seul un accès réel à l'attribut échoue.
- **Un test à marshalling réel** : un dispatcher épinglé à un thread dédié joue le rôle du
  thread principal, et un document factice enregistre tout accès venu d'ailleurs. La liste
  des violations doit rester vide — c'est la garantie que l'ancien code n'avait pas.
- **Coalescence des deltas** (~120 ms ou ~80 caractères) : sans elle, un flux rapide poste
  un événement UNO par token et sature la file du thread principal — l'application
  redeviendrait molle alors même qu'on vient de la libérer.
- **Annulation** : `threading.Event` consulté entre chaque chunk, chaque étape et chaque
  outil ; le bouton d'envoi devient « Arrêter » pendant un run.

## Sort de chaque constat de qualification

**Corrigés (9)**

| # | Sév. | Correction | Commit |
|---|---|---|---|
| R-01 | majeur | Les 6 tests d'intégration repassent. Trois causes distinctes, toutes des tests non maintenus après une évolution délibérée du code (assistant d'enrôlement, dérivation de l'URL d'autorisation, garde de récursion). | `78b4248` |
| R-02 | **bloquant** | `Ctrl/Cmd+Q` n'est plus détourné : `Accelerators.xcu` ne déclare plus qu'un binding, `Ctrl+Alt+Espace` (macOS `Ctrl+Opt+Espace`). | `fea611b` |
| R-03 | majeur | Idem — `Ctrl+E/R/L/K/T/G` rendus à LibreOffice. | `fea611b` |
| R-04 | majeur | « Documentation » et « Site mirai » fonctionnent en Calc : les actions non textuelles passent par un dispatch unique en amont, valable dans les deux modules et même sans document. | `16d000c` |
| R-05 | majeur | Une sélection vide ne fait plus disparaître les actions : le handler Writer ne traite que les actions textuelles, et l'absence de cible produit une consigne explicite. | `16d000c` |
| R-06 | majeur | Une action inconnue est journalisée avec son contexte et signalée à l'utilisateur. | `16d000c` |
| R-07 | **bloquant** | Le `except Exception: pass` qui enveloppait tout `handle_calc_action` journalise et affiche désormais l'erreur. | `16d000c` |
| S-01 | majeur | Hôtes internes remplacés par des placeholders ; `config.default.dgx.json` rejoint les profils gitignorés avec un `.example` versionné ; **le build refuse de produire un OXT si un tel nom réapparaît** dans un fichier suivi par git — vérifié en le réintroduisant volontairement. | `78b4248` |
| T-01 | **bloquant** | Deux niveaux. (1) Plus aucun `processEventsToIdle` dans le cœur ; le run vit dans un worker. (2) **Le chemin legacy s'est manifesté en vrai** le 2026-07-26 pendant un enrôlement SSO : abort + interblocage sur le SolarMutex (voir « Incident » ci-dessous). Une garde `pump_events()` rend désormais tout pompage hors thread principal inoffensif et tracé, dans la coquille comprise. | `2cb3912`, `323efd3` |

**Corrigés en documentation (3)**

| # | Correction | Commit |
|---|---|---|
| D-01 | La notice ne promet plus une entrée de menu inexistante ; elle décrit l'accès réel. | `78b4248` |
| D-02 | `⌘J`, annoncé partout sans avoir jamais existé, a disparu du README et de la notice. | `78b4248` |
| D-03 | Les mentions upstream avaient déjà été retirées ; vérifié après fusion. | `fea611b` |

**Partiellement traités (3)**

| # | État | Ce qui reste |
|---|---|---|
| A-01 | La chaîne `/llm/v1` (`llmToken`, `llmEndpoint`, `llmTokenExpiresAt`, reprise après 401) est présente et **vérifiée en LibreOffice réel** contre le DM local. | Rien côté plugin. Le parcours SSO complet n'a pas pu être rejoué faute de Keycloak local. |
| Q-03 | 259 lignes de code mort supprimées (5 fonctions, chacune vérifiée non référencée). | `clear_rebind_required` (4 l., `security_flow.py`) laissée : ce module est candidat à une décision d'ensemble (voir S-02). |
| Q-04 | `pyproject.toml` + ruff branchés ; `03-test-local.sh` compile enfin **tous** les modules (`core/` et `ui/` en étaient absents) et joue les tests d'intégration. | 8 diagnostics de complexité subsistent dans `core/`+`ui/` (voir « Budgets » ci-dessous). |

**Non traités, et pourquoi (7)**

| # | Sév. | Raison |
|---|---|---|
| A-02 | bloquant | **Risque résiduel le plus important.** Le drapeau `enrolled` court-circuite toujours le ré-enrôlement (`[ENROLL] Auto-check: already enrolled, skipping wizard`), alors que la fonction qui fait autorité — `_relay_credentials_valid()` — existe déjà et est utilisée ailleurs. Le correctif tient en une condition, mais il touche le gate d'enrôlement de la coquille : le modifier sans pouvoir rejouer un parcours SSO complet serait imprudent. **À traiter en premier à la reprise.** |
| A-03 | majeur | Conséquence directe d'A-02. |
| A-04 | mineur | Nommage des clés Keycloak (4 variantes) : cosmétique, touche la coquille, sans effet fonctionnel. |
| S-02 | majeur | `security_flow.py` (831 l.) exige `cryptography`, absent du Python de LibreOffice et non installable (contrainte no-pip) : la télémétrie sécurisée ne fonctionne sur **aucun** poste, et le repli legacy est silencieux. C'est une **décision de produit** (embarquer une implémentation Ed25519 pure-python, ou acter le repli et documenter ces 831 lignes comme inertes), pas un correctif de démonstrateur. |
| S-03 | mineur | Le serveur de callback PKCE se lie sur `0.0.0.0:28443` au lieu de `127.0.0.1`. Une ligne, mais dans le chemin SSO — non testable sans Keycloak. |
| T-02/03/04 | majeur | Trois threads du **cœur legacy** écrivent dans des contrôles VCL sans SolarMutex. Ils vivent dans du code que le commit de nettoyage doit supprimer (étape 8 du plan, non atteinte) : les corriger serait réparer ce qu'on s'apprête à effacer. **Ils restent atteignables tant que les anciens menus existent.** |
| Q-01/Q-02 | majeur | 285 `except … : pass` et 89 fonctions > 40 lignes : le gros du volume est dans `entrypoint.py`, dont le découpage en mixins (étape 20ter) n'a pas été atteint. |

## Métriques, avant et après

| Métrique | `master` | Aujourd'hui | |
|---|---|---|---|
| Tests unitaires | 224 | **421** | +197 |
| Tests d'intégration | 2 échecs / 6 | **6 / 6** | ✅ |
| `processEventsToIdle` dans `core/`+`ui/` | 3 | **0** | vérifié par test |
| Lignes `core/` + `ui/` | 2 816 | 4 172 | +1 356 (dispatcher, sélection, tests) |
| `entrypoint.py` | 9 889 | 10 114 | +225 — voir ci-dessous |
| Code mort | 266 l. | **7 l.** | −259 |
| Diagnostics ruff (`src/`) | 369 | 69 | dont 0 bloquant sur `core/`+`ui/` |
| Linter configuré | aucun | `pyproject.toml` | ✅ |
| Hôtes internes committés | 5 fichiers | **0** | gate de build |

**`entrypoint.py` a grossi de 225 lignes** alors que le plan visait ~300 lignes au total.
C'est l'écart le plus visible avec l'objectif : le découpage en mixins n'a pas été fait, et
les corrections de dispatch y ont ajouté du code. Le solde net (+225) recouvre −259 de code
mort, +45 de dispatch des actions de coquille, et le reste venu de la fusion de `master`.

## Ce qui a été vérifié en conditions réelles

LibreOffice 25.8.4.2, macOS 26, OXT 0.0.1.0.29, DM local Docker + Ollama (`llama3.2`).

| Vérification | Preuve (`~/log.txt`, 2026-07-26) |
|---|---|
| L'extension se charge | `=== mirai extension registered successfully ===` |
| Les identifiants relais partent sur `/config` | `DM config fetch headers: relay=yes keys=[… X-Relay-Client, X-Relay-Key …]` |
| Le DM mint un `llmToken` et le plugin le persiste | `[persist] llm_api_tokens synced from DM (268 chars)` |
| **La palette s'ouvre** | `trigger called: action=OpenAssistant src=key` → `[palette] ouverture demandée app=writer` → `[palette] ouverte` |
| La télémétrie atteint le DM local | `Telemetry trace sent successfully: AssistantOpen, status: 202` |
| Le menu contextuel s'enregistre sur `onLoad` | `[ctx-add] qi-iface(docEvent:OnLoad): registerContextMenuInterceptor OK` |
| Sans document, l'assistant le dit | `[palette] composant courant sans Text/Sheets` + boîte de message |
| Aucune exception | 0 `Traceback` sur la session complète |

**Chaîne LLM de bout en bout**, validée côté serveur (hors plugin) :
`POST /enroll` → paire relais → `/config` avec `X-Relay-*` → `llmToken` → appel réel sur
Ollama → `HTTP 200`, réponse `QUALIF-OK`, bloc `usage` renvoyé spontanément.

**Non vérifié faute de temps ou de moyens** — dit franchement :

- Un run LLM déclenché **depuis la palette** (chip ou prompt libre) jusqu'à l'insertion dans
  le document. La chaîne d'authentification est vérifiée, la palette s'ouvre, mais le
  chaînon final n'a pas été exercé en interactif.
- Le non-blocage pendant un run long (taper dans le document pendant la génération) : c'est
  vérifié par construction et par tests, **pas à l'œil**.
- L'indicateur de sélection en direct, l'annulation, le rendu visuel des chips sur une seule
  ligne : le code est là et testé unitairement, l'aspect n'a pas été constaté à l'écran.
- Les **étapes 15 à 18** (zone basse à onglets, suggestions, redimensionnement,
  Documentation au menu) n'avaient pas été traitées dans la nuit : arbitrage de
  temps au profit des correctifs de qualification. Elles sont **faites** depuis.
- ~~Le parcours SSO complet~~ → **fait le 2026-07-26 contre le DM Scaleway** : login
  Keycloak réel, `POST /enroll` accepté, paire relais reçue, `llmToken` minté et persisté,
  puis appel LLM réel sur `llama-3.3-70b-instruct` renvoyant la réponse attendue avec son
  bloc `usage`. Trace : `[llm-auth] vector=llmToken expires_in=3595s proxy_mode=True
  relay_creds=yes enrolled=True`.
- ~~Un run déclenché depuis la palette jusqu'à l'insertion dans le document~~ →
  **fait** : `Document réécrit : 45 → 2 paragraphe(s) (titre conservé)`, tracé dans
  le fil persisté.
- La persistance du fil est vérifiée : `assistant_conversation.json`, 40 entrées,
  4,2 Ko, restauré à la réouverture (plafond 20 échanges / 100 Ko, troncature FIFO).

**Mesuré, et qui décide du chemin d'exécution** — sonde de capacités sur trois
modèles via Ollama, le 2026-07-26 :

| Modèle | Accepte les outils | En appelle un | Enchaîne lecture → écriture |
|---|:--:|:--:|:--:|
| `llama3.2` | ✓ | ✓ | ✓ |
| `gemma4:12b` | ✓ | ✓ | ✗ — lit puis s'arrête |
| `mistral` | ✓ | ✗ | ✗ — répond du texte |

Trois comportements distincts derrière ce qu'un seul drapeau (`llm_tool_mode`)
prétendait résumer. Seule la troisième colonne détermine si le mode agentique
peut modifier le document : sans elle, le run se termine en succès en laissant le
document intact — exactement le « ça ne fait rien » de la famille 4.

**Restant à constater par un humain**, après les correctifs de fin de journée :

- L'affichage effectif des trois onglets (le correctif `setText` est vérifié par
  relecture programmatique, pas à l'œil).
- Le redimensionnement qui agrandit réellement les champs.
- « Ajouter à la suite » avec ses marqueurs, **dans les deux portées** — sélection
  et document entier. Les deux chemins partagent désormais `presets.text_sink` et
  un test d'architecture le verrouille, mais seul l'usage le prouve.
- L'absence de gel dans la durée (la pompe mesure 0,0 % de CPU au repos, ce qui
  est un indice, pas une preuve d'usage prolongé).
- Le journal des actions affichant `✓ Lecture du document` là où il affichait
  `✗ … maximum 20000`.

C'est précisément l'objet de `docs/RECETTE.md`.

## Incident du 2026-07-26 — le crash « latent » ne l'était pas

Après bascule sur le DM Scaleway, l'enrôlement SSO réussit puis **LibreOffice cesse de
répondre au moindre clic**. Le prélèvement de pile (`sample <pid>`) donne l'enchaînement
sans ambiguïté :

```
thread Python → processEventsToIdle() → DispatchUserEvents → std::terminate() → abort
  → gestionnaire de signal → boîte de récupération d'urgence → SolarMutex
```

Le thread fautif meurt **en tenant le SolarMutex** ; le thread principal reste bloqué dans
`SalYieldMutex::doAcquire`. Ce n'est donc pas une lenteur, c'est un interblocage définitif.

**Signature de diagnostic** (utile car le symptôme ne désigne jamais le coupable) :
l'application est vivante — `STAT=S`, ~1 % de CPU —, le journal s'arrête net au milieu d'une
opération, et le thread principal apparaît dans `doAcquire` sous un `_handleMouseDownEvent`.
Le thread `pythread_wrapper` porte alors le `processEventsToIdle` fautif.

**Ce que ça dit de la qualification.** T-01 avait été classé « crash latent, non corrigé car
il vit dans le code que l'étape de nettoyage doit supprimer ». C'était une erreur de
jugement : un défaut reste **atteignable tant qu'il n'est pas supprimé**, et le nettoyage
n'était pas au programme de cette campagne. Une garde bon marché aurait évité un diagnostic
à chaud.

**Correction.** Un helper `pump_events(toolkit)` vérifie le thread avant de pomper :
inchangé sur le thread principal, no-op **journalisé** ailleurs. 18 sites convertis, aucun
appel direct ne subsiste. La garde s'est déclenchée dès la première exécution :

```
[threading] processEventsToIdle ignoré : appel depuis 'Thread-5' et non le thread principal
```

L'enrôlement aboutit alors normalement et LibreOffice reste réactif.
`tests/unit/test_pump_events_safety.py` (6 tests) verrouille la garde, dont un contrôle AST
qui interdit tout appel direct hors du helper. Le plan porte désormais le **piège n°23**
avec la signature de diagnostic complète.

## Budgets de qualité

| Métrique | Budget | `core/` + `ui/` |
|---|---|---|
| Diagnostics ruff (hors complexité) | 0 | **0** ✅ |
| Complexité cyclomatique ≤ 10 | 0 dépassement | **8** ❌ |
| Fonctions > 40 lignes | 0 | **17** ❌ |
| Module ≤ 400 lignes | 0 dépassement | `palette.py` (≈ 800), `presets.py` (≈ 570) ❌ |

Les 8 dépassements de complexité : `_run_step`, `_coerce`, `_validate_value`,
`parse_json_tool_calls`, `_first_balanced_object`, `run_transform`, `run_stream`,
`write_result_column`. Toutes préexistantes sauf `run_stream`. Le linter les **mesure et les
affiche** à chaque exécution de `03-test-local.sh` : le chiffre reste sous les yeux.

La factorisation `run_text_pipeline` (n°1 des huit cibles nommées du plan) n'a pas été faite.
C'est elle qui ferait tomber les runners de presets à ~15 lignes déclaratives et supprimerait
les arguments d'interface inutilisés aujourd'hui tolérés par `per-file-ignores` — chaque
exception est commentée dans `pyproject.toml` avec sa raison.

## Un bug trouvé en chemin, hors qualification

`log_to_file` pouvait lever. Comme ses appels sont disséminés au milieu de chemins critiques
eux-mêmes enveloppés dans des `except Exception` larges, une panne de **journalisation** se
transformait en perte silencieuse de données : dans `_persist_bootstrap_config`, un log qui
lève entre deux `set_config` faisait perdre `llmTokenExpiresAt` — donc un jeton LLM sans date
d'expiration, rejoué jusqu'au 401. C'est exactement le mécanisme décrit par Q-01, pris sur le
fait. Corrigé dans `2cb3912`.

Il a été trouvé parce que l'ajout de tests a changé l'ordre d'exécution de la suite. Une
seconde fragilité du même ordre a été corrigée dans la foulée : `MainJob.__init__` lance un
rafraîchissement de configuration en tâche de fond qui réécrit `config.json`, et les tests
d'authentification entraient en course avec lui — l'échec se déplaçait d'un test à l'autre
selon la charge de la machine. La suite est désormais stable sur trois exécutions
consécutives.

## Risques résiduels, par ordre d'importance

1. **A-02 — l'état absorbant n'a pas de porte de sortie.** Un poste qui atteint
   `enrolled=true` sans paire relais reste bloqué en 401 : le drapeau court-circuite le
   ré-enrôlement qui le sauverait. La fonction qui fait autorité existe déjà
   (`_relay_credentials_valid`). **Premier chantier à la reprise.**
2. **Le cœur legacy est toujours là**, avec ses trois threads qui écrivent dans des contrôles
   VCL sans SolarMutex (T-02/03/04). Ils sont atteignables tant que les anciens menus
   existent. L'étape 8 du plan — commit de pure suppression — les emporte.
3. **La télémétrie sécurisée ne fonctionne nulle part** (S-02) et le repli est silencieux :
   on croit émettre des traces signées, on émet du legacy.
4. **`entrypoint.py` reste à 10 114 lignes.** Le découpage en mixins est mécanique et sûr
   (les tests n'importent que `MainJob`), mais volumineux ; il n'a pas été entamé.
5. Le démonstrateur n'a **pas** été exercé de bout en bout en interactif : voir la liste
   « non vérifié » ci-dessus.

## Ce que cette journée apprend sur la méthode

**Aucun de ces dix-neuf défauts n'a été trouvé par les tests.** Ils l'ont tous été
en se servant du plugin, et diagnostiqués par la mesure — jamais par déduction.
Trois outils ont fait tout le travail :

- `sample <pid>` distingue en dix secondes un **interblocage** (thread principal
  dans `SalYieldMutex::doAcquire`) d'un **thread principal au repos** avec des
  messages non délivrés. Deux gels d'apparence identique, deux causes opposées.
- **Une trace en fin de run.** Si `run: terminé` s'affiche alors que l'interface
  reste figée, ce n'est ni un blocage ni une exception : ce sont les messages qui
  ne passent pas. Cette seule ligne a fait gagner trois tentatives.
- **La relecture après écriture.** `posé=1888, relu=1888` prouve que la donnée
  est dans le contrôle ; il ne reste alors que le rendu. Sans elle, on cherche
  indéfiniment du côté des données.

Le corollaire vaut d'être écrit : **j'ai proposé quatre correctifs successifs au
même symptôme avant de trouver la cause**, et l'un d'eux a aggravé la situation.
Chaque fois qu'on corrige sans avoir mesuré, on ajoute du code sur un diagnostic
faux. La règle qui en sort : *devant un symptôme d'interface, instrumenter les
trois niveaux — donnée présente, contrôle visible, contenu écrit — avant de
toucher au code.*

Second enseignement : **une suite instable est un signal, pas une nuisance.** Les
deux échecs intermittents rencontrés cachaient de vraies courses — un thread de
rafraîchissement qui écrasait la configuration, et une écriture non atomique qui
pouvait faire perdre les credentials en production.

Troisième enseignement, apparu en fin de journée : **un symptôme déjà rencontré
n'a pas la même cause que la fois précédente.** « Ça ne fait rien » est revenu
quatre fois avec quatre causes indépendantes (famille 4). Ce symptôme-là est
pauvre en information — il dit seulement que la chaîne s'est interrompue quelque
part — et l'expérience acquise sur les occurrences précédentes devient un
handicap : elle oriente vers une hypothèse déjà corrigée. La discipline qui en
sort : **d'abord établir quel chemin a été emprunté** (télémétrie de preset,
présence de la trace « Document réécrit », journal des actions), et seulement
ensuite chercher la défaillance sur ce chemin-là.

Quatrième enseignement : **chercher les incohérences de traitement entre
contraintes voisines.** Le rejet sur `maximum` face à la troncature sur
`maxLength` cohabitaient depuis l'origine, à douze lignes d'écart, chacune
défendable prise isolément. Ce type de défaut ne se voit ni en relecture locale
ni en test unitaire — chaque branche est correcte — mais seulement en se
demandant *ces deux règles expriment-elles la même intention ? alors pourquoi ne
se comportent-elles pas pareil ?*

## Ce que je ferais ensuite, dans cet ordre

1. A-02 (une condition, gros effet), puis A-03.
2. Étape 8 du plan : supprimer le cœur legacy — ce qui règle T-02/03/04 et une bonne part de
   Q-01/Q-02 par soustraction.
3. Dérouler `RECETTE.md` en interactif et corriger ce qu'il révèle —
   en priorité les scénarios 3.9 à 3.11 et 8.2, ajoutés après la session de recette
   et **non encore validés par un humain**.
4. Étape 20ter : découpage d'`entrypoint.py` en mixins.
5. Factorisation `run_text_pipeline`, puis les sept autres cibles nommées.
6. Trancher S-02 (Ed25519 pur-python, ou repli assumé et documenté).
