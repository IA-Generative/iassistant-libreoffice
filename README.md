# MIrAI — Assistant LibreOffice


## Table des matières

- [Fonctionnalités Writer](#fonctionnalités-writer)
- [Fonctionnalités Calc](#fonctionnalités-calc)
- [Comportement général](#comportement-général)
- [Installation](#installation)
- [Déploiement et mises à jour](#déploiement-et-mises-à-jour)
- [Configuration](#configuration)
- [Structure du dépôt](#structure-du-dépôt)
- [Scripts de développement](#scripts-de-développement)
- [Télémétrie et monitoring](#télémétrie-et-monitoring)
- [Device Management et bootstrap](#device-management-et-bootstrap)
- [Historique des mises à jour](#historique-des-mises-à-jour)
- [License](#license)

---

## Fonctionnalités Writer

> **Accès unique — `Ctrl+Alt+Espace`** (macOS : `Ctrl+Opt+Espace`), ou l'entrée
> **🤖 Ouvrir l'assistant** du menu **🤖 MIrAI**, ou le clic droit sur une sélection dans Writer.
> Les raccourcis par fonction ont été **supprimés** : ils écrasaient des commandes
> de LibreOffice (`Ctrl+Q` = Quitter, `Ctrl+E` = centrer, `Ctrl+R` = aligner à
> droite, `Ctrl+L` = aligner à gauche, `Ctrl+K` = insérer un hyperlien), et l'un
> d'eux — `Ctrl+J` — était annoncé partout sans avoir jamais existé.
> Chaque fonction ci-dessous est une **chip** de la palette.

### ✏️ Modifier la sélection

Ouvre un dialogue avec des suggestions IA contextuelles. Saisir une instruction libre (traduction, reformulation, correction…) ou choisir une suggestion. Le résultat remplace la sélection. Filtrage automatique des blocs `<think>` (deepseek-r1).

### 📏 Ajuster la longueur

Mini-dialogue flottant avec boutons **−** (réduire ~35%) et **+** (développer ~40%). Remplacement en place, preview streaming, itératif.

### 📝 Résumer la sélection

Résumé concis inséré après la sélection avec délimiteurs.

### 💬 Reformuler la sélection

Reformulation en langage clair, insérée après la sélection avec délimiteurs.

### 📚 Documentation — menu MIrAI

Ouvre l'URL de documentation configurée via le bootstrap (`doc_url`).

---

## Fonctionnalités Calc

### 🔄 Transformer → colonne résultat

Applique une instruction sur une plage de cellules et écrit les résultats dans une colonne adjacente (non destructif).

### 🧮 Générer une formule

Génère une formule LibreOffice Calc à partir d'une description en langage naturel. Injecte automatiquement le contexte de la feuille (en-têtes, plage, valeurs). Boucle de correction si erreur.

### 📊 Analyser la plage

Analyse la plage sélectionnée et insère un résumé des tendances et anomalies sous la sélection.

---

## Comportement général

### Gestion des modèles

- **Modèles deepseek-r1** : les blocs `<think>…</think>` sont filtrés avant insertion
- **Détection de question** : si le modèle pose une question au lieu d'exécuter, l'extension le détecte
- **Retry automatique** : sur 403 après enrollment, refresh config + retry transparent

### Télémétrie

Chaque action génère une trace OpenTelemetry avec :
- `plugin.action` : nom condensé (`extend`, `edit`, `summarize`, `formula`…)
- `trigger.source` : origine (`menu`, `toolbar`, `key`, `auto`)
- Header `X-Client-UUID` envoyé pour identification pré-enrollment

---

## Installation

### Prérequis

- LibreOffice 7.x ou supérieur
- Accès à un backend compatible OpenAI

### Installation de l'extension

```bash
# Construire le paquet OXT
./scripts/02-build-oxt.sh

# Installer
/Applications/LibreOffice.app/Contents/MacOS/unopkg add --force --suppress-license dist/mirai.oxt
```

Ou via l'interface : **Outils → Gestionnaire d'extensions → Ajouter** → sélectionner `dist/mirai.oxt`.

### Cycle de développement

```bash
# Build + install + config profile + launch LibreOffice
./scripts/dev-launch.sh --config config/profiles/config.default.integration.json

# Efface les données locales de l'extension (Keychain, journaux LibreOffice, cache
# temporaire) et désinstalle l'extension
./scripts/00-clean-install.sh --uninstall
```

---

## Déploiement et mises à jour

### Déploiement automatisé via Device Management

Un seul appel fait tout : upload de l'artefact, création de la version, extraction des manifests, création de la campagne.

```bash
# 1. Bump version + build
./scripts/bump-version.sh 0.0.8.0.0

# 2. Commit + push
git add oxt/description.xml dm-manifest.json oxt/registration/license.txt
git commit -m "release: v0.0.8.0.0"
git push

# 3. Déployer
./scripts/deploy-release.sh \
  --bootstrap-url https://bootstrap.fake-domain.name \
  --strategy canary
```

L'endpoint unifié `POST /api/plugins/{slug}/deploy` gère automatiquement :
- Upload et stockage de l'artefact
- Création de la version (deprecation des anciennes)
- Extraction de `dm-config.json` et `dm-manifest.json`
- Création de la campagne de rollout

### Stratégies de rollout

**`canary`** (défaut) — déploiement progressif automatique :

| Palier | Pourcentage | Durée | Description |
|--------|-------------|-------|-------------|
| 1 | 5% | 24h | **Canary** — quelques utilisateurs testent |
| 2 | 25% | 48h | **Early adopters** — validation plus large |
| 3 | 100% | — | **General availability** — tout le monde |

Le pourcentage est calculé par un hash du `client_uuid` — c'est déterministe (le même device est toujours dans le même palier). Les paliers avancent automatiquement en fonction du temps écoulé depuis la création de la campagne.

**`immediate`** — déploiement à 100% immédiatement. Tous les devices reçoivent la mise à jour au prochain appel config.

### Mise à jour automatique côté plugin

1. Le plugin appelle `/config/{slug}/config.json` au démarrage et à chaque action
2. Le DM compare la version du plugin avec la campagne active
3. Si une mise à jour est disponible, le plugin choisit la route. **Native** si le feed `<update-information>` de l'extension installée annonce exactement la version cible : LibreOffice télécharge alors l'OXT lui-même, avec sa pile HTTP, rien ne transite par le plugin. Sinon **dirigée** : le plugin télécharge l'artefact via `/catalog/{slug}/download` — avec **failover multi-bootstrap** (chaque DM essayé en ordre *last-good d'abord*, TLS par-URL) pour ne pas rester bloqué sur une URL injoignable _(#16)_
4. Route dirigée : vérifie le checksum SHA-256. Route native : pas de hash, la confiance repose sur TLS vers le DM
5. **Installe la mise à jour**, avec repli en cascade — un seul primitif, `addExtension` sur le thread principal :
   1. **Route native pilotée** — si le feed `<update-information>` de l'extension installée annonce exactement la version cible, le plugin ouvre le dialogue natif « Mise à jour des extensions » (`PackageManagerDialog`, `SHOW_UPDATE_DIALOG`) : LibreOffice télécharge et installe lui-même, avec sa pile HTTP ; le plugin ferme ensuite LibreOffice proprement, en réessayant tant que la fenêtre de mise à jour est ouverte ; s'il doit y renoncer, la nouvelle version s'active au prochain démarrage. _(#5, #9)_
   2. **Route dirigée** — sinon (amorçage, rollback, feed injoignable ou divergent) : téléchargement avec failover multi-bootstrap, sha256, `addExtension` in-process, fermeture propre. **Aucun processus enfant.** _(#4, #15, #16)_
   3. Sinon, **boîte « mise à jour bloquée »** : mode opératoire manuel + bouton **« Ouvrir le dossier »** (natif, `SystemShellExecute`). _(#7, #12)_
   Un refus n'est pas reproposé avant 24 h.
6. Report du statut au DM via `/update/status` (relay-headers requis)

**Protection anti-boucle** : `target_version` comparée à la version courante (directives identiques ignorées) ; un target dont l'install a été bloquée n'est plus reproposé.

**Diagnostic / test** :
- `MIRAI_SELFTEST_UPDATE_BLOCKED=1` force la boîte « mise à jour bloquée » via *À propos ▸ Vérifier les mises à jour*, sans déployer de MAJ _(#7)_.
- Le dialogue *À propos* expose aussi un bouton **« Ouvrir dossier »** (même ouverture native que ci-dessus) pour tester localement, Mac inclus.

> **Suivi du mécanisme de MAJ** : issue-parapluie **#9** · install in-process **#4** (singleton `.get` **#15**) · download failover **#16** · bouton « Ouvrir le dossier » **#7**/**#12** · option native `<update-information>` **#5** (flux format LibreOffice côté DM : IA-Generative/device-management#4) · cache binaire DM : IA-Generative/device-management#24.

### Suivi et contrôle

```bash
# Progression
curl -s -H "X-Admin-Token: $DM_ADMIN_TOKEN" \
  https://bootstrap.fake-domain.name/api/campaigns/{id}/progress | python3 -m json.tool

# Pause
curl -s -X PATCH -H "X-Admin-Token: $DM_ADMIN_TOKEN" \
  https://bootstrap.fake-domain.name/api/campaigns/{id}/pause

# Abort et rollback
curl -s -X PATCH -H "X-Admin-Token: $DM_ADMIN_TOKEN" \
  https://bootstrap.fake-domain.name/api/campaigns/{id}/abort
```

Documentation complète : [docs/DEPLOY.md](docs/DEPLOY.md)

---

## Configuration

### Via l'interface

**Menu MIrAI → Paramètres** : URL du backend, modèle par défaut, token API, proxy.

### Fichiers de configuration

| Fichier | Rôle |
| --- | --- |
| `config/config.default.json` | Réglages de bootstrap packagés dans l'OXT (URL du DM, profil) |
| `config/profiles/` | Profils prédéfinis (`docker`, `kubernetes`, `integration`, `local-llm`) |
| `dm-config.json` | Configuration DM embarquée (bootstrap URL, profil) |
| `dm-manifest.json` | Métadonnées plugin pour le catalogue DM |
| [`docs/donnees-locales.md`](docs/donnees-locales.md) | Données que l'extension stocke sur le poste |

### Profils de déploiement

| Profil | Usage |
| --- | --- |
| `integration` | Environnement d'intégration/recette |
| `docker` | Bootstrap local Docker Compose |
| `kubernetes` | Template k8s générique |
| `local-llm` | 100 % local, bootstrap désactivé |

---

## Structure du dépôt

```
src/mirai/
├── entrypoint.py              # MainJob, UNO, mise à jour auto, télémétrie
├── security_flow.py           # SecureBootstrapFlow (enrollment, tokens, relay)
├── calc_prompt_function.py    # Fonction Calc add-in (XPromptFunction)
└── menu_actions/
    ├── writer.py              # Edit, Summarize, Simplify, Resize
    ├── calc.py                # Transform, Formula, Analyze
    └── shared.py              # Utilitaires partagés

oxt/                           # Fichiers statiques packagés dans l'OXT
├── Addons.xcu                 # Menus et toolbar
├── Accelerators.xcu           # Raccourcis clavier
├── Jobs.xcu                   # Job d'initialisation
├── icons/                     # Icônes toolbar
└── META-INF/manifest.xml

config/profiles/               # Profils de configuration

scripts/
├── 00-clean-install.sh        # Efface données locales, entrées Keychain, journaux LO, cache extension (--uninstall : retire aussi l'extension)
├── 02-build-oxt.sh            # Produit dist/mirai.oxt
├── dev-launch.sh              # Build + install + launch LibreOffice
├── bump-version.sh            # Bump version + build + instructions deploy
└── deploy-release.sh          # Déploiement unifié via DM

docs/
├── DEPLOY.md                  # Guide de déploiement complet
├── TELEMETRY.md               # Documentation télémétrie OpenTelemetry
└── PLUGIN_DEVELOPER_GUIDE.md  # Onboarding développeur tiers (DM, enrôlement, manifests, WAF)

tests/
├── unit/                      # Tests unitaires (pytest)
├── fixtures/                  # Documents de test
└── simulation/                # Simulateur de déploiement
```

---

## Scripts de développement

```bash
# Efface les données locales de l'extension, puis la désinstalle
./scripts/00-clean-install.sh --uninstall

# Cycle dev (build + install + launch)
./scripts/dev-launch.sh --config config/profiles/config.default.integration.json

# Tests unitaires
python3 -m pytest tests/unit/ -v

# Bump version + build
./scripts/bump-version.sh

# Déployer en intégration
./scripts/deploy-release.sh \
  --bootstrap-url https://bootstrap.fake-domain.name \
  --strategy canary
```

---

## Télémétrie et monitoring

### Traces OpenTelemetry

| Span name | `plugin.action` | Quand |
|---|---|---|
| `ExtensionLoaded` | `launch` | Démarrage du plugin |
| `ExtensionUpdated` | `update` | Après mise à jour |
| `BootstrapConfig` | `bootstrap` | Fetch config DM |
| `EnrollSuccess` | `enroll.ok` | Enrollment réussi |
| `EnrollFailed` | `enroll.fail` | Enrollment échoué |
| `EditSelection` | `edit` | Modifier la sélection |
| `ResizeSelection` | `resize` | Ajuster la longueur |
| `SummarizeSelection` | `summarize` | Résumer |
| `SimplifySelection` | `simplify` | Reformuler |
| `TransformToColumn` | `transform` | Transformer (Calc) |
| `GenerateFormula` | `formula` | Générer formule (Calc) |
| `AnalyzeRange` | `analyze` | Analyser plage (Calc) |

### Configuration

```json
{
  "telemetryEnabled": true,
  "telemetryEndpoint": "https://traces.example.com/v1/traces",
  "telemetryAuthorizationType": "Bearer",
  "telemetryKey": "",
  "telemetrylogJson": false
}
```

---

## Device Management et bootstrap

### Flux de bootstrap sécurisé

```mermaid
sequenceDiagram
  autonumber
  participant P as Plugin
  participant DM as Device Management
  participant KC as Keycloak

  P->>DM: GET /config/{slug}/config.json
  DM-->>P: config publique (sans secrets)

  P->>KC: PKCE login (auth + token)
  KC-->>P: access_token

  P->>DM: POST /enroll (Bearer access_token)
  DM-->>P: relay_client_id + relay_client_key

  P->>DM: GET /config/{slug}/config.json + X-Relay-Client/Key
  DM-->>P: config complète (avec secrets LLM)

  Note over P,DM: Mise à jour automatique
  DM-->>P: update directive (target_version, artifact_url, checksum)
  P->>DM: GET /catalog/{slug}/download
  DM-->>P: fichier .oxt
  P->>P: quit LO → unopkg remove → add → relaunch
```

---

## Historique des mises à jour

| Version | Changements principaux |
| --- | --- |
| 0.0.1.0.4+ | **Stabilité multi-instance** : SIGTERM remplace `desktop.terminate()` (plus de crash depuis un thread background), flags enrollment/update partagés entre instances LO, attente de la config avant tout trigger |
| 0.0.1.0.4+ | **UX update** : popup de mise à jour différée jusqu'après l'enrôlement, réouverture du document actif (et non du Start Center) après auto-update, lancement du script de mise à jour seulement après confirmation utilisateur |
| 0.0.1.0.4+ | **Auto-update fiabilisé** : chemin OXT stable, attente effective de la fermeture de LO, traces dans `~/log.txt`, anti-boucle (skip si déjà à `target_version`) |
| 0.0.8+ | **Télémétrie enrichie** : `plugin.action`, `trigger.source`, `X-Client-UUID` header, traces Calc |
| 0.0.8+ | **Mise à jour automatique** : download via catalog, checksum, staged install cross-platform, anti-boucle |
| 0.0.8+ | **Fix UI** : filtrage `<think>` dans Edit, suggestions IA sync (pas de gel), retry 403 |
| 0.0.8+ | **Déploiement** : endpoint unifié `/api/plugins/{slug}/deploy`, `bump-version.sh`, rollout canary/immediate |
| 0.2.0 | **Ajuster la longueur**, suggestions IA, générateur formules, dialogue À propos, toolbar, deploy automatisé |
| 0.1.0 | Générer, Modifier, Résumer, Reformuler (Writer), Transformer, Formule, Analyser (Calc), enrollment Keycloak |

> **Développeur de plug-in tiers ?** Tout ce qu'il faut connaître pour intégrer un plug-in (Writer/Calc, navigateur MV3, Thunderbird, MS Office) au Device Management — manifests, enrôlement, sécurité, contournement WAF, auto-update — est consolidé dans [docs/PLUGIN_DEVELOPER_GUIDE.md](docs/PLUGIN_DEVELOPER_GUIDE.md).

---

## License

- Voir `registration/license.txt`

Dépôt de référence :
- [IA-Generative/AssistantMiraiLibreOffice](https://github.com/IA-Generative/AssistantMiraiLibreOffice) — ce dépôt
