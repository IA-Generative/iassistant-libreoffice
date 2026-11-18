# Télémétrie fonctionnelle — rapport d'exécution et de recette

**Date** : 2026-07-27
**Branches** : `feat/telemetrie-fonctionnelle` (plugin) · `feat/telemetry-typed-attributes` (device-management)
**Base** : `exp-jetable/demonstrateur-v2` · `fix/campaign-plugin-filter`

---

## 1. Pourquoi

`core/telemetry_steps.py` avait posé le socle — span unique `AssistantStep`, vocabulaire
fermé, filtre `safe_attributes` garantissant par construction qu'aucune phrase du document
ne quitte le poste. Il n'était branché que sur neuf étapes de la palette.

Le parcours du code a révélé que l'essentiel du fonctionnement restait invisible :

- **`AssistantRun` n'existait que pour le mode agentique.** Les trois autres chemins
  d'exécution — preset pipeline, réécriture de sélection, réécriture de document — ne
  laissaient aucune trace : ni durée, ni issue, ni cause. Or ce sont eux qui traitent la
  majorité des demandes sur un poste dont le modèle n'enchaîne pas les outils.
- **Aucune cause de fin.** Une annulation, un plafond d'itérations et un HTTP 429 se
  ressemblaient tous : `ok=false`.
- **Les mécanismes de rattrapage étaient à sens unique.** La bascule définitive
  natif→JSON, la reprise après 401 et la reprise après « budget mangé par le
  raisonnement » n'étaient tracées qu'en échec. Impossible de dire si elles servent.
- **Les refus et les gestes d'interface n'existaient pas.** Un preset cliqué à vide tous
  les jours ne produisait qu'un statut rouge que personne ne remonte.
- **Trois pannes muettes** : filet anti-blocage de la palette, attente bloquante de
  configuration (jusqu'à 15 s), action de menu non implémentée.

---

## 2. Ce qui a été livré

### Plugin — 4 commits

| Commit | Contenu |
|---|---|
| `4a6d264` | Socle : 7 constantes au vocabulaire fermé + `emit_run` (span de run sous le même filtre anti-contenu) |
| `efe2aed` | **Lot A — fiabilité** : span `AssistantRun` unifié sur les 4 chemins, cause de fin, motif d'échec des outils, coercitions d'arguments, bascules et reprises LLM |
| `b60d9b9` | **Lot B — usage** : refus de lancement, session de palette agrégée, `AssistantOpen` enrichi, refocus |
| `9877cf5` | **Lot C — santé** : filet anti-blocage, attente de configuration, action non gérée |
| `7876848` | Test de contrat moteur → fil → serveur |

### Device Management — 2 commits

| Commit | Contenu |
|---|---|
| `a0b89c4` | Lecture des attributs OTLP **typés** à la persistance SQL |
| `0fc2767` | La mise en lettre morte ne tue plus le worker de la file |

### Le point d'émission unique du span de run

L'orchestrateur n'émet plus `AssistantRun` : son `RunResult` portait déjà tout ce que le
span disait. L'émission passe dans le `finally` de `_run_in_worker`
([palette.py](../src/mirai/ui/palette.py)), seul endroit que les quatre branches
traversent — exceptions et fermeture de palette comprises. Deux émetteurs auraient fait
diverger deux formats du même span.

Attributs : `run.kind` (`agentic` | `pipeline` | `selection_rewrite` | `document_rewrite`),
`assistant.preset`, `assistant.ok`, `assistant.cancelled`, `assistant.reason`,
`assistant.mode`, `assistant.iterations`, `assistant.duration_ms`, `append.mode`.

Causes de fin possibles : `cancelled`, `max_iterations`, `max_iterations_after_changes`,
`http_401`/`http_429`/…, `network_error`, `truncated`, `reasoning_starved`, `empty_reply`,
`model_question`, `target_changed`, `palette_closed`, `exception`.

Une demande libre sur un document sans texte de corps (vide, ou fait de titres seuls)
rédige le texte demandé au lieu de réécrire : ses étapes sont `document.read` puis
`document.rewrite.done`, avec `body.paragraphs` à 0, sans `document.rewrite.start`.

### Volumétrie maîtrisée

Une trace coûte une requête HTTP et un thread, sans mise en lot. Les gestes d'interface
(onglets, panneau de raisonnement, nouvelle conversation, bascules « Ajouter à la suite »,
consultations des Suggestions) sont donc **comptés en mémoire et émis en un seul span à la
fermeture**. Seuls les clics de l'utilisateur comptent, jamais les bascules
programmatiques. Deux refus consécutifs de même cause ne comptent qu'une fois (Entrée
martelée). Une action non gérée n'est signalée qu'une fois par nom et par session.

---

## 3. Deux défauts trouvés en recette

### 3.1 Les compteurs arrivaient vides en base *(corrigé)*

`_persist_telemetry_spans` ne lisait que `stringValue`. Depuis que le plugin encode ses
attributs avec les types OTLP, **tout entier ou booléen arrivait en chaîne vide** dans
`device_telemetry_events` — donc dans la vue « activité appareil » de l'admin. Tempo, lui,
recevait le corps verbatim et ne perdait rien : le défaut ne se voyait que côté SQL.

Chaque compteur ajouté par ce lot aurait été silencieusement perdu. Corrigé par un lecteur
typé (`stringValue` → `intValue` → `boolValue` → `doubleValue`), rétrocompatible.

### 3.2 Une tâche en échec tuait le worker de la file *(corrigé)*

`queue_job_dead_letters.job_id` ne portait aucune contrainte unique, alors que
`move_to_dead_letter` fait `ON CONFLICT (job_id)`. Postgres refuse la requête
(`InvalidColumnReference`), l'exception remonte la boucle du worker et **la tue** : la file
entière — télémétrie **et enrôlements** — cesse d'être traitée jusqu'au redémarrage du
conteneur.

Le déclencheur est banal : il suffit qu'une tâche épuise ses huit tentatives. Un amont OTLP
injoignable le provoque — c'est ainsi qu'il a été découvert. Une coupure de l'observabilité
emportait donc le traitement des enrôlements avec elle.

Corrigé par un index unique dans le schéma canonique, appliqué de façon idempotente au
démarrage.

---

## 4. Recette

### 4.1 Tests automatisés

| Suite | Résultat |
|---|---|
| Plugin — `./scripts/03-test-local.sh` (unitaires + intégration + lint + build) | **657 tests verts**, aucun échec, lint propre, OXT produit |
| Device Management — tests ajoutés (`test_telemetry.py`, `test_queue_validation.py`) | **11 verts** |
| Device Management — suite complète | **aucune régression** — voir ci-dessous |

#### Device Management : 88 échecs, strictement les mêmes avant et après

La suite du DM n'est pas verte sur cette machine, et ne l'était pas non plus avant cette
livraison. Pour distinguer une régression d'un échec d'environnement, chaque version a été
passée **deux fois** (branche, puis commit parent `4f217bf` en tête détachée) :

| | run 1 | run 2 | échecs constants |
|---|---|---|---|
| Commit parent | 88 | 88 | **88** |
| Cette branche | 89 | 90 | **88** |

Le jeu des 88 échecs constants est **strictement identique** des deux côtés : aucune
régression. Les écarts entre runs individuels sont des tests instables —
`test_queue_load_smoke` (seuil de débit `throughput_rps > 500.0`, sensible à la charge
machine ; il passe seul) et deux `test_e2e_deployment.py` qui sollicitent le réseau.

Répartition des 88 :

| Fichier | Nb | Cause |
|---|---|---|
| `test_e2e_deployment.py` | 35 | exigent un déploiement vivant et une base `bootstrap` locale |
| `test_post_deploy.py` | 32 | interrogent de vraies URL (`httpx.ConnectError`) |
| `test_admin_ui.py` | 18 | exigent une session Keycloak (404) |
| 3 autres | 3 | passent **seuls**, échouent en suite complète (pollution entre modules : `importlib.reload(app.main)` avec des variables d'environnement différentes) |

> **Correction d'une affirmation antérieure.** Une version précédente de ce rapport
> annonçait « 22 échecs avant / 21 après, dont un corrigé par le lot
> (`test_queue_load_smoke`) ». C'était faux : la mesure reposait sur un seul run par
> version, et ce test est instable. Il n'est pas corrigé par cette livraison — il passe ou
> échoue selon la charge de la machine.

Environ 90 tests ajoutés, écrits **avant** le code et vérifiés rouges (37 échecs initiaux
côté plugin, 2 côté DM) avant toute implémentation.

Le test de contrat [`test_telemetry_wire_contract.py`](../tests/integration/test_telemetry_wire_contract.py)
parcourt le trajet complet sans réseau — attributs émis → encodage OTLP de la coquille →
relecture par la logique du serveur — et couvre **chaque étape du vocabulaire fermé**. Il
échoue si l'un des trois maillons cesse de préserver les types. C'est le test qui manquait :
les tests unitaires s'arrêtaient à l'appel de `shell.telemetry`, précisément là où le défaut
3.1 se logeait.

### 4.2 Recette locale bout en bout (DM Docker + Tempo)

Huit spans représentatifs — un par nouveauté — fabriqués par le **vrai encodeur du plugin**
(`otel_attributes`, `generate_trace_id`), postés sur le vrai endpoint du DM local, puis
relus en base :

```
POST /telemetry/v1/traces × 8      → 202 Accepted
file telemetry.forward             → 8 done, 0 pending
device_telemetry_events            → 8 lignes
Tempo (service mirai-libreoffice)  → 8 traces
✓ tous les spans persistés avec leurs types intacts
```

Chaque attribut émis non textuel revient non textuel, à la même valeur — vérifié
individuellement par appariement sur un marqueur d'identité, l'ordre de traitement de la
file n'étant pas garanti. Exemples relus en base :

```json
AssistantRun          {"assistant.ok": false, "assistant.cancelled": false,
                       "assistant.duration_ms": 8421, "append.mode": true}
AssistantToolCall     {"tool.ok": false, "tool.duration_ms": 42, "tool.args_coerced": 2}
AssistantStep/closed  {"runs.count": 4, "tabs.switches": 7, "session.duration_ms": 754321}
AssistantOpen         {"selection.active": true, "caps.measured": false}
```

#### Recette avec le VRAI code de la palette (2026-07-28)

La recette ci-dessus postait des payloads **écrits à la main** : elle prouvait que le serveur
sait relire des types, pas que le plugin émet les bonnes valeurs. Une seconde passe comble
l'écart — la palette réelle est montée sur des contrôles UNO factices, ses parcours sont
rejoués (refus, run preset, réécriture de document, run annulé, gestes d'interface,
fermeture), et `shell.telemetry` est branché sur le **vrai** émetteur OTLP vers le DM local.
Aucune valeur n'est écrite à la main :

```
5 spans émis PAR LE CODE → 202 → file → 5 lignes en base → 5 traces dans Tempo
✓ chaque valeur produite par le code est relue à l'identique en base
```

Valeurs produites par les parcours, relues sans altération :

```json
AssistantStep/run.refused  {"refuse.reason": "empty_prompt"}
AssistantRun               {"run.kind": "pipeline", "assistant.ok": true, "append.mode": false}
AssistantRun               {"run.kind": "document_rewrite", "assistant.reason": "empty_reply",
                            "append.mode": true}
AssistantRun               {"run.kind": "agentic", "assistant.cancelled": true,
                            "assistant.reason": "cancelled", "assistant.iterations": 2}
AssistantStep/palette.closed {"runs.count": 3, "tabs.switches": 2, "suggestions.views": 1,
                            "reasoning.opens": 1}
```

Les compteurs de session correspondent exactement aux gestes rejoués — trois runs, deux
clics d'onglet dont un sur Suggestions, une ouverture du panneau de raisonnement.

**Un point relevé au passage** : les cinq spans portaient `assistant.duration_ms: 0`.
Plausible, toutes les branches du harnais étant instantanées — mais indistinguable d'un
compteur cassé. Vérifié en faisant durer un run pour de vrai (356 ms mesurées), et verrouillé
par un test permanent (`test_the_run_duration_is_measured_and_not_hardcoded`) : sans lui, une
régression sur la mesure passerait inaperçue, tous les runs de la suite étant instantanés.

**Configuration locale ajustée** (fichiers non versionnés, `deploy/docker/.env.secrets`) :
`DM_TELEMETRY_TOKEN_SIGNING_KEY` était vide et écrasait la valeur de `.env` — le mint de
jeton répondait 503 ; `DM_TELEMETRY_UPSTREAM_ENDPOINT` pointait un collecteur inexistant.
Sauvegarde dans `.env.secrets.bak-e2e`. La pile d'observabilité locale (Tempo + Grafana) a
été démarrée : `docker compose -p mirai-obs -f deploy/docker/local-rcfg/docker-compose.observability.yml up -d`.

### 4.3 Intégration Scaleway — **vérifiée verte**

> **Résultat final (2026-07-28)** : les cinq déploiements sont sur
> `0.9.15-telemetry1` et la recette passe — `✓ tous les spans persistés avec leurs
> types intacts`. Le détail ci-dessous retrace le chemin, car la première bascule
> avait échoué pour une raison qui resservira.

```
POST /telemetry/v1/traces × 8   → 200 (telemetry-relay persiste en synchrone)
device_telemetry_events         → 8 lignes, types intacts
file / lettres mortes           → 0 en attente, 0 lettre morte
index unique lettre morte       → idx_queue_job_dead_letters_job_id présent
```

Relu en base d'intégration :

```json
AssistantRun          {"assistant.ok": false, "assistant.cancelled": false,
                       "assistant.duration_ms": 8421, "append.mode": true}
AssistantToolCall     {"tool.ok": false, "tool.duration_ms": 42, "tool.args_coerced": 2}
AssistantStep/closed  {"runs.count": 4, "tabs.switches": 7, "session.duration_ms": 754321}
AssistantOpen         {"selection.active": true, "caps.measured": false}
```

À comparer à l'état d'avant bascule de `telemetry-relay`, où **tous** ces attributs
valaient `""`.

#### Le chemin — bascule d'abord incomplète

Image construite et poussée : `docker.io/etiquet/device-management:0.9.15-telemetry1`
(linux/amd64, digest `sha256:89d6997a…`). Le tag `latest` n'a délibérément **pas** été
écrasé. Périmètre vérifié : l'intégration exécutait déjà le contenu de
`fix/campaign-plugin-filter`, la bascule se limite donc aux deux commits de télémétrie.

`deploy/device-management` a été basculé le 2026-07-28 — **et le retest a échoué** : les
huit spans arrivent bien en base, mais **tous les attributs typés y sont vides**, exactement
le comportement d'avant correctif.

#### La topologie d'intégration n'est pas celle du Docker local

C'est la leçon de cette recette. En local, un seul conteneur fait tout. En intégration,
**cinq déploiements partagent la même image**, et l'ingress répartit les chemins :

| Chemin | Service | Rôle |
|---|---|---|
| `/telemetry/v1` | **`telemetry-relay`** | ingère les traces **et les persiste en synchrone** (réponse 200, pas 202 : la file n'est pas utilisée pour la télémétrie en intégration) |
| `/` | `device-management` | API |
| `/admin` | `device-management-admin` | vue « activité appareil » |
| `/llm` | `llm-proxy` | proxy LLM |
| — | **`queue-worker`** (×2) | boucle de traitement de la file — c'est **elle** que le défaut de lettre morte tue |

Basculer `deploy/device-management` ne touche donc **ni** le service qui persiste la
télémétrie, **ni** celui qui exécute la boucle de file. Les deux correctifs de cette
livraison visent précisément ces deux déploiements-là.

#### Commandes restantes

```bash
IMAGE=docker.io/etiquet/device-management:0.9.15-telemetry1

# indispensable : c'est lui qui persiste la télémétrie
kubectl -n bootstrap set image deploy/telemetry-relay telemetry-relay=$IMAGE
# indispensable : c'est sa boucle que la lettre morte tuait
kubectl -n bootstrap set image deploy/queue-worker queue-worker=$IMAGE
# cohérence de version (aucun changement de comportement attendu)
kubectl -n bootstrap set image deploy/device-management-admin device-management-admin=$IMAGE
kubectl -n bootstrap set image deploy/llm-proxy llm-proxy=$IMAGE

for d in telemetry-relay queue-worker device-management-admin llm-proxy; do
  kubectl -n bootstrap rollout status deploy/$d --timeout=180s
done

# retour arrière : kubectl -n bootstrap rollout undo deploy/<nom>   (→ 0.9.15-ui1)
```

> Le nom du conteneur est repris du nom du déploiement ; vérifier au besoin avec
> `kubectl -n bootstrap get deploy <nom> -o jsonpath='{.spec.template.spec.containers[*].name}'`.

#### Retest

```bash
DM_URL=https://bootstrap.fake-domain.name DM_PROFILE=int DM_NAMESPACE=bootstrap \
  python3 <scratchpad>/e2e_telemetry.py
```

Attendu : `✓ tous les spans persistés avec leurs types intacts`. Contrôle direct :

```sql
SELECT span_name, attributes FROM device_telemetry_events
 ORDER BY id DESC LIMIT 8;
```

Les valeurs numériques et booléennes doivent apparaître **sans guillemets**. État constaté
avant la bascule de `telemetry-relay` — le défaut que corrige cette livraison :

```json
{"e2e.case": "", "caps.agentic": "", "caps.measured": "", "selection.active": "",
 "assistant.app": "writer", "plugin.action": "assistant.open"}
```

L'index unique de lettre morte, lui, était **déjà appliqué** en base dès la première
bascule : le schéma est réappliqué au démarrage, et seul le pod porteur du DSN
administrateur y parvient. Il ne protège toutefois `queue-worker` qu'une fois ce
déploiement basculé, puisque c'est son code qui exécute la boucle — ce qui est
désormais le cas.

#### Tests de post-déploiement passés contre l'intégration

`tests/test_post_deploy.py` est prévu pour viser une instance vivante et ne modifie rien
(lecture seule, en-tête du fichier). Lancé contre l'intégration :

```bash
DM_BASE_URL=https://bootstrap.fake-domain.name \
DM_ADMIN_TOKEN=<secret device-management-secrets/DM_QUEUE_ADMIN_TOKEN> \
  pytest tests/test_post_deploy.py
```

**29 / 32 verts.** Les trois échecs sont des résolutions DNS
(`httpx.ConnectError: nodename nor servname provided`) sur des URL *annoncées par la config*
et hébergées sur le réseau interne du ministère, injoignable depuis un poste externe. Ils ne
dépendent pas du code livré ici.

`tests/test_e2e_deployment.py` (35 tests) n'a **pas** été lancé contre l'intégration, et ne
doit pas l'être : il est écrit pour un déploiement Docker local créé de zéro — il vérifie des
conteneurs et des ports locaux, et sa phase 4 **crée** cohortes, drapeaux, artefacts et
campagnes. Le passer sur un environnement partagé y écrirait de vrais objets.

#### La chaîne Scaleway fonctionne, mais le parc est dirigé ailleurs

Les trois profils servis par `bootstrap.fake-domain.name` — `int`, `prod` et `dev` — annoncent
tous le **même** `telemetryEndpoint` :

```
https://<HOTE_DGX>/telemetry/v1/traces
```

**Ce n'est pas un défaut de configuration, c'est un choix délibéré.** La cause est une
surcharge à chaud enregistrée en base (`config_overrides`), posée depuis l'interface
d'administration le **2026-07-27 à 12:42 UTC par johann.lorber-linagora@interieur.gouv.fr** :

| Clé | Valeur |
|---|---|
| `PUBLIC_BASE_URL` | `https://<HOTE_DGX>/bootstrap` |
| `DM_BOOTSTRAP_URLS` | `["https://<HOTE_DGX>"]` |

Ces surcharges priment sur l'environnement du pod, qui porte pourtant
`PUBLIC_BASE_URL=https://bootstrap.fake-domain.name` et un
`DM_TELEMETRY_PUBLIC_ENDPOINT=/telemetry/v1/traces` relatif.
`_resolve_public_telemetry_endpoint()` (app/main.py:893) dérive l'endpoint de l'origine de
`PUBLIC_BASE_URL` : la valeur onyxia en découle mécaniquement. Tout se comporte comme conçu.

Conséquence à connaître, à ne pas confondre avec une panne :

- **La chaîne Scaleway est saine et vérifiée** — la recette la vise directement et passe.
- **Aucun poste réel n'y envoie ses traces** tant que cette surcharge est en place : le parc
  est dirigé vers DGX. Le correctif de typage n'y produira donc aucun effet visible.
- Le namespace `dm-dgx-test` de ce cluster tourne l'image **`0.7.0`**. Impossible de
  confirmer depuis l'extérieur que `<HOTE_DGX>` dessert bien ce déploiement — le
  nom ne résout pas hors du réseau interne.

> **Aucune modification n'a été faite sur ces surcharges.** Elles relèvent d'une décision
> d'exploitation prise par un tiers sur un environnement partagé : les changer redirigerait
> la télémétrie d'un parc entier. À arbitrer avec leur auteur.

#### Une erreur de démarrage, préexistante et sans lien

`telemetry-relay` journalise au démarrage `Failed to apply DB schema` →
`must be owner of table feature_flags`. Le partage des privilèges veut que seul le pod
porteur du DSN administrateur (`postgres`, propriétaire des tables) applique le schéma ;
les autres échouent au premier ordre exigeant la propriété. C'est structurel et antérieur
à cette livraison : l'ordre en cause est à la ligne 361 de `db/schema.sql`, non modifié
ici, alors que l'index ajouté est à la ligne 102 — il est donc atteint *avant*, et il est
bien créé. Sans effet sur le service, mais mériterait d'être traité pour ne pas polluer
les journaux.

---

## 5. Points ouverts

Tous suivis en issues (2026-08-01) :

| # | Sujet | Dépôt |
|---|---|---|
| [#35](https://github.com/IA-Generative/AssistantMiraiLibreOffice/issues/35) | `writer_replace_paragraphs` s'affiche sous son nom technique dans le journal | plugin |
| [#36](https://github.com/IA-Generative/AssistantMiraiLibreOffice/issues/36) | Suggestions non cliquables alors que le moteur le prévoit | plugin |
| [#37](https://github.com/IA-Generative/AssistantMiraiLibreOffice/issues/37) | Recette de la télémétrie dans un LibreOffice réel | plugin |
| [#38](https://github.com/IA-Generative/AssistantMiraiLibreOffice/issues/38) | Spans `Assistant*` jetés en silence avant liaison utilisateur | plugin |
| [#20](https://github.com/IA-Generative/device-management/issues/20) | Trois pods sur cinq journalisent une pile d'erreurs au démarrage | DM |
| [#21](https://github.com/IA-Generative/device-management/issues/21) | Trois tests passent isolément et échouent en suite | DM |
| [#22](https://github.com/IA-Generative/device-management/issues/22) | `test_queue_load_smoke` : seuil de débit instable | DM |
| [#16](https://github.com/IA-Generative/device-management/issues/16) | Versions divergentes entre environnements — DGX en `0.7.0` (commenté) | DM |

Hors issues, deux gestes d'exploitation :

- **Bascule du parc sur Scaleway** — une surcharge posée le 2026-07-27 par
  johann.lorber-linagora@interieur.gouv.fr (`PUBLIC_BASE_URL`, `DM_BOOTSTRAP_URLS`) dirige
  tout le parc vers `<HOTE_DGX>`. Rien n'a été modifié : à arbitrer avec son auteur.
- **Les deux PR se fusionnent ensemble** — sans le correctif serveur, les compteurs du
  plugin arrivent vides côté admin.
