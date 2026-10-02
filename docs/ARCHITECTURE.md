# Architecture — Démonstrateur « moteur MCP interne + palette universelle »

> ⚠️ Branche `exp-jetable/demonstrateur-v2` — **expérimentation jetable**,
> ne pas merger vers master. Ce document est la carte pour qu'un humain **et**
> un assistant de codage puissent reprendre le code sans archéologie.

## Vue en couches

```
┌────────────────────────────────────────────────────────────┐
│ COQUILLE (inchangée) — src/mirai/entrypoint.py (MainJob)   │
│ enrollment · Keycloak/SSO · device management · auto-update│
│ télémétrie · proxy/_urlopen · SSL · config bootstrap       │
└────────────┬───────────────────────────────────────────────┘
             │ trigger("OpenAssistant") → import paresseux
┌────────────▼───────────────┐   ┌───────────────────────────┐
│ core/entry.py (le pont)    │──▶│ ui/palette.py (DSFR)      │
│ MainJobShell(job)          │   │ chips · prompt · sélection│
└────────────┬───────────────┘   │ zone basse à onglets :    │
             │ ShellServices     │ Historique/Suggest./Actions│
┌────────────▼───────────────────┴───────────────────────────┐
│ MOTEUR (core/) — jamais d'import de la coquille (testé)    │
│ registry (tools MCP-like) · orchestrator (boucle agentique)│
│ llm_client (natif/JSON) · sse_pump · sinks · presets       │
│ conversation (persistance MVP) · tools/writer · tools/calc │
└────────────────────────────────────────────────────────────┘
```

## Règles non négociables

1. **Threading — modèle worker + dispatcher** (itération 2, remplace le drain).
   Le run **entier** vit dans un thread worker ; le thread principal retourne
   immédiatement à la boucle d'événements de LibreOffice, qui reste utilisable
   pendant toute la génération. Tout ce qui touche UNO — document, contrôles,
   undo, exécution des tools — repasse par `MainThreadDispatcher`
   (`core/ui_thread.py`) :
   - `post(fn)` pour l'affichage (sans attendre) ;
   - `call(fn, timeout)` pour lire ou modifier le document (rend le résultat).

   **Plus aucun `processEventsToIdle` dans `core/` ni `ui/`** — vérifié par
   `test_core_and_ui_never_pump_events` (analyse AST, pas du texte). La classe
   de gel (et l'abort `std::terminate` dans `DispatchUserEvents`) devient
   impossible par construction, au lieu d'être évitée par vigilance.

   Corollaires :
   - **La reprise d'authentification après 401 fait du réseau bloquant** : elle
     vit dans le thread du pump (via la fabrique de requête de `run_stream`),
     jamais sur le thread principal. Même famille de piège que le drain.
   - **La sélection se lit en PUSH** (`XSelectionChangeListener`, livré par
     LibreOffice sur le thread principal), jamais par un thread qui interroge
     en boucle. Le listener est retiré **avant** `dispose()`.
   - **Anti-flood** : les deltas sont coalescés (~120 ms ou ~80 caractères)
     avant d'être postés, sinon la file du thread principal sature et
     l'application redevient molle.
   - **Coût du rendu** : la coalescence ne sert à rien si chaque flush
     recompose tout l'affichage. Le coût par flush doit être proportionnel au
     fragment, pas à l'historique — et un rendu ne relit JAMAIS une source
     persistée (l'historique du fil est en cache, invalidé quand il change).
   - **Pompe** : `AsyncCallback` posté depuis un worker ne réveille PAS la
     boucle d'événements ; la tâche attend le prochain geste de l'utilisateur.
     Les tâches passent donc par une file, drainée par une pompe qui tourne sur
     le thread principal et **se réarme elle-même** — armée au démarrage du run
     (depuis le thread principal), éteinte à sa fin. 0,1 % de CPU au repos.
     Il n'existe pas de timer UNO : `com.sun.star.awt.Timer` renvoie `null`.
   - **La pompe s'éteint dès que la file est vide** — se réarmer sans
     condition monopolise la boucle et gèle l'interface immédiatement.
   - **Écrire du texte** : `model.Text` porte l'état, `control.setText()`
     l'affiche. Un contrôle déjà doté d'un peer ne repeint pas sur la seule
     écriture du modèle — passer par `_set_text()`, jamais par `model.Text`
     directement.
   - **Filet d'état** : les mises à jour de fin de run sont postées, donc
     perdables. `heal_if_stuck()` (sur `windowActivated`) restaure l'interface
     si elle est « occupée » sans worker vivant. Toute machine à états pilotée
     par messages asynchrones a besoin d'un tel filet.
2. **Zéro import de la coquille** dans `core/` et `ui/` — la façade
   `shell_facade.MainJobShell` duck-type l'objet MainJob. Règle exécutable :
   `tests/unit/core/test_no_entrypoint_import.py`.
3. **Pas de pip** : stdlib uniquement (validateur JSON-schema = sous-ensemble
   maison documenté dans `core/tool_calls.py`).
4. **Jamais de contenu documentaire en télémétrie** — uniquement compteurs,
   statuts, durées, noms de tools.
5. **Marianne jamais embarquée** (licence État) — sonde runtime dans
   `ui/dsfr.py` (Marianne → Arial → Liberation Sans).

## Les tools (miroir MCP interne)

| Tool | App | Mutant | Rôle |
|---|---|---|---|
| writer_get_selection | W | | texte sélectionné |
| writer_get_document_map | W | | doc en paragraphes numérotés [Pn] |
| writer_replace_paragraphs | W | ✔ | réécrit [Pstart]…[Pend] ; `\n` = nouveaux paragraphes |
| writer_replace_selection | W | ✔ | remplace la sélection (court) |
| writer_insert_text | W | ✔ | insère après sélection / fin de doc |
| writer_find_replace | W | ✔ | paires find/replace exactes |
| calc_get_selection | C | | plage, dimensions, en-têtes, aperçu |
| calc_read_range | C | | plage → tableau texte |
| calc_get_sheet_overview | C | | structure de la feuille |
| calc_write_cells | C | ✔ | écrit des cellules |
| calc_write_result_column | C | ✔ | colonne « Résultat IA » non destructive |
| calc_set_formula | C | ✔ | formule + relecture d'erreur (Err:…) |
| calc_fill_formula_down | C | ✔ | recopie avec décalage de lignes |

Un run d'orchestrateur = **un seul contexte undo** (ouvert par le premier tool
mutant, fermé en finally) → l'action complète s'annule d'un Ctrl+Z.

Principe clé (petits modèles) : **la prose longue ne transite jamais en
argument JSON** — elle est streamée en réponse finale vers un *sink*
(`sinks.py` : PaletteSink, WriterInsertSink, WriterReplaceSink, CalcCellSink).

**Les capacités du modèle sont MESURÉES, pas supposées** (`core/capabilities.py`).
Trois capacités distinctes : le relais accepte `tools` (A), le modèle appelle un
outil (B), il enchaîne lecture → écriture (C). Seul A était sondé ; c'est C qui
décide du chemin. Mesuré sur Ollama : llama3.2 = A✓B✓C✓, gemma4:12b = A✓B✓C✗,
mistral = A✓B✗C✗. La sonde (2 allers-retours) est déclenchée depuis le menu
« Tester le modèle », son verdict mis en cache par couple (endpoint, modèle) et
annoncé à l'utilisateur. Sans mesure, on suppose C faux — le chemin déterministe
aboutit toujours, le mode agentique peut échouer en silence.

**Le tool calling est une commodité, jamais une garantie.** Sur un modèle de
taille moyenne, une demande de réécriture du document donnait `iterations=2` :
lecture appelée, écriture jamais. Trois renforts de prompt n'y ont rien changé.
Dès qu'une action DOIT aboutir, elle est pilotée depuis Python — le LLM n'est
alors qu'une fonction texte et le résultat est appliqué par le code
(`core/doc_rewrite.py`, même patron que les presets pipeline). Le mode agentique
reste pour l'exploration et les demandes ouvertes.

**Bascule vers le chemin déterministe : on énumère les QUESTIONS, pas les
ordres.** Sans sélection, tout ce qui n'est pas une demande d'information est
un ordre portant sur le document. Énumérer les verbes de modification est sans
fin ; les questions forment un ensemble fermé. Attention à l'ordre poli
(« peux-tu restructurer… ? ») : seule l'ouverture compte, pas le « ? ».

**Règle de portée : l'IHM annonce, le modèle n'infère pas.** L'orchestrateur
préfixe chaque demande d'une ligne de PORTÉE calculée sur le document —
sélection courante, ou « aucune sélection ⇒ document entier, de [P1] au
dernier ». Ce qui va de soi pour l'utilisateur n'est visible nulle part pour le
modèle. Le rappel d'action vit en outre DANS le résultat de l'outil de lecture,
là où il arrive juste avant la décision, plutôt que dilué dans le préambule.
Diagnostic : `assistant.iterations` = 1 ⇒ aucun outil appelé ; = 2 ⇒ lecture
sans écriture.

**Jauge d'activité — `core/progress.py`.** Pendant un run : champ de saisie et
chips grisés, rotor braille rafraîchi ~200 ms, phase courante (Réflexion /
Rédaction / Action sur le document), compteur de jetons et chronomètre. Le
compteur est une estimation locale (caractères ÷ 4) **marquée `~`**, remplacée
par la valeur exacte si le relais envoie spontanément un bloc `usage` — jamais
réclamé, car `stream_options` fait rejeter la requête par certains relais.

**Les titres sortent de la plage réécrite.** Préserver le style de chaque
paragraphe ne suffit pas : si la plage commence par un titre, le premier bloc
de corps s'y déverse et s'affiche en style Titre. `doc_rewrite.body_range()`
borne la réécriture aux paragraphes de corps ; les titres sont donnés au modèle
comme contexte, avec consigne de ne pas les reprendre.

**Écriture de configuration atomique.** `set_config` écrit dans un fichier
temporaire puis `os.replace`. En place, un lecteur concurrent peut voir un JSON
tronqué, repartir sur les valeurs par défaut et perdre les credentials — soit
une entrée non désirée dans l'état absorbant.

**Règle d'écriture : jamais de `setString` sur une plage multi-paragraphes.**
LibreOffice applique alors le style du PREMIER paragraphe à tout le bloc — un
document titre + corps repart intégralement en style titre. On écrit donc
paragraphe par paragraphe (`para.setString`), on supprime le surplus par
`removeTextContent`, et les paragraphes ajoutés héritent du style du **dernier**
remplacé. `writer_get_document_map` annote les styles (`[P1] <Heading 1>`) et
termine par `[FIN DU DOCUMENT — N paragraphes]` : sans ces deux repères, le
modèle fusionne un titre avec le corps, ou s'arrête avant la fin.

**Règle de complétude du catalogue.** Tout outil de LECTURE doit avoir son
pendant d'ÉCRITURE à la même granularité, sinon le modèle sait décrire ce
qu'il faudrait faire sans pouvoir le faire — et l'utilisateur voit « il ne se
passe rien » alors que le run se termine en succès. C'est ce qui manquait à
`writer_get_document_map` : il numérotait les paragraphes que rien ne savait
réécrire, si bien qu'une demande de restructuration sans sélection recevait une
réponse en texte et laissait le document intact.
Symptôme à reconnaître : télémétrie `iterations=1`, `ok=true`, document
inchangé ⇒ **aucun outil appelé** — regarder le catalogue avant le moteur.

Le prompt système porte la contrepartie : *« AGIS, NE DÉCRIS PAS »* — appliquer
la modification avec les outils d'écriture, et traiter l'absence de sélection
comme « la demande porte sur le document entier ».

## Presets (les chips de la palette) — `core/presets.py`

- **pipeline** (Python pilote, iso-fonctionnalité stricte avec l'historique,
  marqueurs `---début-du-…---` conservés) : Résumer, Simplifier,
  Raccourcir/Allonger, Transformer (Calc), Analyser (Calc).
- **agentique** (le LLM pilote les tools) : Formule (Calc — contexte de feuille
  + retrieval `config/calc-functions.json`) et le **prompt libre**, qui couvre
  désormais les demandes documentaires (« restructure en deux paragraphes »)
  grâce à `writer_replace_paragraphs`.
- Les chips « Continuer » et « Modifier » ont été retirées : le prompt libre
  les couvre, et une rangée courte tient sur une seule ligne (piège n°17).
- Télémétrie : spans historiques conservés (`SummarizeSelection`, …) avec
  `{"via": "palette"}` + nouveaux spans `assistant.open/run/tool`.

## Client LLM double-mode — `core/llm_client.py`

- `llm_tool_mode` : `auto` (défaut) | `native` | `json` — distribuable par DM.
- `auto` : tools OpenAI natifs d'abord ; HTTP 400/404/422 sur une requête
  portant des tools → bascule définitive en JSON (cachée dans
  `llm_tool_mode_detected`).
- Mode JSON : catalogue + protocole dans le prompt système ; parseur tolérant
  (fences, `<think>`, virgules traînantes, quotes typographiques) ; deltas
  retenus tant que la réponse ressemble à un tool call, **flush intégral si le
  parse échoue** — la sortie du modèle n'est jamais perdue.
- Clamps max_tokens par modèle réappliqués par la façade
  (`shell_facade.MODEL_TOKEN_LIMITS`) car `make_chat_request` ne les a pas.

## Persistance de conversation (MVP) — `core/conversation.py`

`<UserConfig>/mirai/assistant_conversation.json` — 20 échanges / 100 Ko max,
écriture atomique, tolérant à la corruption, local uniquement, bouton
« Nouvelle conversation ». Les tours user + réponses finales seulement
(jamais les tool calls). Injection des derniers échanges dans le contexte
(cap ~4 000 caractères).

## Fichiers locaux et secrets — modules partagés de `src/mirai/`

- `local_config.py` : dossier `<UserConfig>/mirai/`, écritures atomiques, aucune valeur secrète
  (`redact_dm_config` vide les clés de `SECRET_DM_KEYS` avant `dm_snapshot.json`), effacement
  complet à la désinstallation. Voir `docs/donnees-locales.md`.
- `credentials.py` : jetons courts en mémoire, secrets durables dans le coffre de l'OS
  (Gestionnaire d'identification Windows, trousseau macOS, mémoire ailleurs). Partagé avec
  l'add-in `=PROMPT()` sous le même nom de module.
- `log_setup.py` : journal `mirai/mirai.log`, rotation 1 Mo × 1. Ni jeton, ni réponse `/config`
  brute du DM, ni corps de requête envoyé au modèle, ni texte du document (les lignes `[journal]`
  n'y portent que la taille des extraits, affichés dans l'onglet « Actions »).

## Ajouter un tool en 5 étapes

> Avant d'écrire quoi que ce soit, poser les trois questions de complétude :
> existe-t-il (a) une lecture pour se repérer, (b) une écriture de même
> granularité, (c) une consigne du prompt système qui impose d'utiliser la
> seconde ? Répondre « non » à l'une des trois donne un assistant qui commente
> au lieu d'agir.

1. Handler `def mon_tool(ctx, args) -> ToolResult` dans `core/tools/…`.
2. `registry.register(ToolSpec(name="app_mon_tool", description=…, parameters=
   {schema}, handler=…, apps=(…), mutates=bool))` dans `register()`.
3. Libellé FR dans `ui/palette.py:TOOL_LABELS` (journal d'actions).
4. Test dans `tests/unit/core/test_tools_uno.py` (fakes `tests/stubs/fake_docs.py`).
5. Si le tool sert un preset : câbler dans `core/presets.py` + test golden.

## Tests

`python3 -m pytest tests/unit/ -v` — 337 tests (coquille inchangée + moteur).
Golden iso-fonctionnels : `test_presets_writer.py` / `test_presets_calc.py`
rejouent chaque fonction historique via le moteur (FakeShell + SSE scripté +
faux documents à état réel).

## De démonstrateur à produit (dette nommée, si on poursuit)

1. Supprimer le cœur legacy d'entrypoint.py + `menu_actions/` (commit de pure
   suppression — prévu, non fait tant que la validation utilisateur n'est pas
   passée) puis, une release plus tard, `stream_request`/`make_api_request`.
2. Unifier `=PROMPT()` (`calc_prompt_function.py`) sur la façade (≈150 lignes
   dupliquées de la coquille, sans Keycloak ni relais).
3. `ui_ask_user` (questions de clarification interactives dans la palette).
4. i18n ; multi-conversations + recherche ; sidebar historique ;
   permissions par tool ; rafraîchir les chips quand l'app change sous une
   palette ouverte.

## Environnement de dev macOS — piège connu

macOS 26 (Darwin 25.5) tue les binaires auxiliaires de LibreOffice.app
(`uno`, `unopkg`, python embarqué) : SIGKILL « Launch Constraint Violation »
→ `unopkg add` et l'install in-process échouent. Réparation (2026-07-25) :
re-signature ad hoc (`codesign --force -s - <binaire>`) de
`Contents/MacOS/{uno,unopkg,gengal,regview,senddoc,unoinfo,uri-encode,xpdfimport,opencltest}`
et du framework Python embarqué. **À refaire après chaque mise à jour de
LibreOffice.app.**
