# Deployer une nouvelle version MIrAI dans Device Management

## Prerequis

- Acces au repo `AssistantMiraiLibreOffice`
- Variable `DM_ADMIN_TOKEN` configuree (token admin DM)
- URL du bootstrap DM (ex: `https://bootstrap.fake-domain.name`)

## Deploiement automatise (recommande)

Un seul appel fait tout : upload de l'artefact, creation de la version,
extraction des manifests, creation de la campagne de rollout.

### 1. Preparer la version

```bash
# Bump version, build, affiche les instructions
./scripts/bump-version.sh 0.0.8.0.0

# Commit + push
git add oxt/description.xml dm-manifest.json oxt/registration/license.txt
git commit -m "release: v0.0.8.0.0"
git push
```

### 2. Deployer

```bash
# Canary (rollout progressif — recommande)
./scripts/deploy-release.sh \
  --bootstrap-url https://bootstrap.fake-domain.name \
  --config config/profiles/config.default.integration.json \
  --strategy canary

# Ou immediat (100% direct)
./scripts/deploy-release.sh \
  --bootstrap-url https://bootstrap.fake-domain.name \
  --config config/profiles/config.default.production.json \
  --strategy immediate
```

L'environnement cible est defini par `--bootstrap-url` + `--config` (sans
`--config`, le build prend `config/config.default.json`, fichier local non suivi).
Les profils `config.default.integration.json` et `config.default.production.json`
sont a creer depuis les `.example` (gitignores).

Le script utilise l'endpoint unifie `POST /api/plugins/{slug}/deploy` qui
fait tout en une seule requete :

```bash
curl -X POST https://bootstrap.fake-domain.name/api/plugins/mirai-libreoffice/deploy \
  -H "X-Admin-Token: $DM_ADMIN_TOKEN" \
  -F "binary=@dist/mirai.oxt" \
  -F "strategy=canary"
```

Reponse :

```json
{
  "ok": true,
  "plugin_id": 6,
  "version": "0.0.8.0.0",
  "version_id": 12,
  "artifact_id": 9,
  "campaign_id": 10,
  "checksum": "sha256:cb16c2b9...",
  "strategy": "canary"
}
```

### 3. Suivre le deploiement

```bash
# Progression de la campagne
curl -s -H "X-Admin-Token: $DM_ADMIN_TOKEN" \
  https://bootstrap.fake-domain.name/api/campaigns/{id}/progress \
  | python3 -m json.tool

# Console admin (navigateur)
open "https://bootstrap.fake-domain.name/admin/campaigns"
```

### 4. Pause / Abort

```bash
# Mettre en pause
curl -s -X PATCH -H "X-Admin-Token: $DM_ADMIN_TOKEN" \
  https://bootstrap.fake-domain.name/api/campaigns/{id}/pause

# Annuler et rollback
curl -s -X PATCH -H "X-Admin-Token: $DM_ADMIN_TOKEN" \
  https://bootstrap.fake-domain.name/api/campaigns/{id}/abort
```

## Strategies de rollout

### `canary` (defaut)

Deploiement progressif en 3 paliers automatiques :

| Palier | Pourcentage | Duree | Description |
|--------|-------------|-------|-------------|
| 1 | 5% | 24h | **Canary** — quelques utilisateurs testent |
| 2 | 25% | 48h | **Early adopters** — validation plus large |
| 3 | 100% | - | **General availability** — tout le monde |

Le pourcentage est calcule par un hash du `client_uuid` du device.
C'est **deterministe** : le meme device est toujours dans le meme palier.
Les paliers avancent **automatiquement** en fonction du temps ecoule depuis
la creation de la campagne — aucune action manuelle requise.

Timeline typique :
- **T+0** : 5% des devices recoivent la mise a jour
- **T+24h** : 25% des devices
- **T+72h** : 100% des devices

### `immediate`

Deploiement a 100% immediatement, sans palier. Tous les devices recoivent
la mise a jour au prochain appel config.

A utiliser pour :
- Corrections de securite urgentes
- Environnements de test/integration
- Petites populations de devices

### Options supplementaires

```bash
# Cibler un cohort specifique
./scripts/deploy-release.sh \
  --bootstrap-url https://bootstrap.fake-domain.name \
  --strategy canary \
  --cohort-id 3

# Urgence critique (affichage different cote plugin)
./scripts/deploy-release.sh \
  --bootstrap-url https://bootstrap.fake-domain.name \
  --strategy immediate \
  --urgency critical
```

## Deploiement manuel (sans script)

### Etape 1 — Mettre a jour la version

Modifier ces 3 fichiers avec le nouveau numero de version :

| Fichier | Champ |
|---------|-------|
| `oxt/description.xml` | `<version value="X.Y.Z"/>` |
| `dm-manifest.json` | `changelog[0].version` |
| `oxt/registration/license.txt` | `version X.Y.Z` |

### Etape 2 — Builder le package

```bash
./scripts/02-build-oxt.sh --config config/profiles/config.default.integration.json
```

Resultat : `dist/mirai.oxt`

### Etape 3 — Deployer via l'endpoint unifie

```bash
curl -X POST \
  https://bootstrap.fake-domain.name/api/plugins/mirai-libreoffice/deploy \
  -H "X-Admin-Token: $DM_ADMIN_TOKEN" \
  -F "binary=@dist/mirai.oxt" \
  -F "strategy=canary" \
  -F "urgency=normal"
```

L'endpoint gere automatiquement :
- Upload et stockage de l'artefact (upsert)
- Creation de la version (upsert + deprecation des anciennes)
- Extraction de `dm-config.json` et `dm-manifest.json` depuis le `.oxt`
- Creation de la campagne (auto-complete les anciennes)

## Rollback

```bash
# Deployer l'ancienne version en urgence
./scripts/deploy-release.sh \
  --bootstrap-url https://bootstrap.fake-domain.name \
  --config config/profiles/config.default.production.json \
  --version 0.0.7.0.0 \
  --strategy immediate
```

## Comment ca marche cote plugin

1. Le plugin appelle `/config/{slug}/config.json` au demarrage et a chaque action
2. Le DM compare la version du plugin (`X-Plugin-Version` header) avec la campagne active
3. Si une mise a jour est disponible, le DM renvoie un bloc `"update"` dans la reponse
4. Le plugin choisit la route : **native** si le feed `<update-information>` de
   l'extension installee annonce exactement la version cible (LibreOffice
   telecharge l'OXT lui-meme) ; sinon **dirigee** : telechargement via
   `/catalog/{slug}/download` avec failover multi-bootstrap, puis verification
   du checksum SHA-256
5. Installation par `addExtension` sur le thread principal, en cascade : route
   native pilotee (dialogue natif "Mise a jour des extensions"), sinon route
   dirigee (`addExtension` in-process, aucun processus enfant), sinon boite
   "mise a jour bloquee" avec bouton "Ouvrir le dossier". Apres installation,
   le plugin ferme LibreOffice proprement. Un refus n'est pas repropose avant 24 h
6. Le plugin rapporte le statut au DM via `/update/status`

Details : [update-natif-libreoffice.md](update-natif-libreoffice.md)

## Simulation (test sans impact)

```bash
# Simuler 100 devices
python3 tests/simulation/deploy_simulator.py \
  --devices 100 \
  --bootstrap-url https://bootstrap.fake-domain.name \
  --profile int
```
