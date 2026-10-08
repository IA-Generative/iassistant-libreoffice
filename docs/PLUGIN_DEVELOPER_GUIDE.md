# Plugin Developer Guide — Intégration au Device Management MIrAI

> **Public visé** : développeurs qui veulent créer un nouveau plug-in (LibreOffice, navigateur, Thunderbird, MS Office, futur…) qui se rattache au backend **Device Management** (DM) du programme MIrAI, ou qui veulent maintenir/étendre les plug-ins existants.
>
> **Repos de référence** :
> - [`AssistantMiraiLibreOffice`](https://github.com/IA-Generative/AssistantMiraiLibreOffice) — plug-in Writer/Calc, Python (UNO)
> - [`mirai-assistant-navigateur`](https://github.com/IA-Generative/mirai-assistant-navigateur) — extension navigateur Chrome/Firefox MV3, JS
> - [`device-management`](https://github.com/IA-Generative/device-management) — backend FastAPI + Postgres + relay-assistant nginx
>
> **TL;DR** : un plug-in MIrAI fournit deux fichiers de manifest (`dm-config.json`, `dm-manifest.json`), implémente un cycle de vie en 5 étapes (config → auth PKCE → enroll → relay-call → telemetry) et respecte trois règles d'or de sécurité réseau (JSON+b64, jamais multipart > 2 MB, headers `X-Relay-*`). Le reste se construit autour.

---

## Table des matières

1. [Panorama : qu'est-ce qu'un plug-in MIrAI ?](#1-panorama--quest-ce-quun-plug-in-mirai-)
2. [Principes généraux par plate-forme](#2-principes-généraux-par-plate-forme)
3. [Le Device Management — vue plug-in](#3-le-device-management--vue-plug-in)
4. [Cycle de vie d'un plug-in](#4-cycle-de-vie-dun-plug-in)
5. [Manifests embarqués dans l'artefact](#5-manifests-embarqués-dans-lartefact)
6. [Manifests de déploiement (DM côté serveur)](#6-manifests-de-déploiement-dm-côté-serveur)
7. [Sécurité, authentification, topologie réseau](#7-sécurité-authentification-topologie-réseau)
8. [Échanges réseau et précautions WAF](#8-échanges-réseau-et-précautions-waf)
9. [Exemples de code clés](#9-exemples-de-code-clés)
10. [Checklist : créer un nouveau plug-in en 1 jour](#10-checklist--créer-un-nouveau-plug-in-en-1-jour)
11. [Annexes](#11-annexes)

---

## 1. Panorama : qu'est-ce qu'un plug-in MIrAI ?

Un **plug-in MIrAI** est une extension applicative (LibreOffice, navigateur, etc.) qui :

- s'**enrôle automatiquement** auprès d'un serveur DM la première fois qu'elle s'exécute,
- **récupère sa configuration** (URLs LLM, modèles, tokens, paramètres SSO) à distance, sans secret embarqué dans le binaire publié,
- **se met à jour seule** (ou via le store, selon la plate-forme), pilotée par des **campagnes canary**,
- **émet de la télémétrie OpenTelemetry** signée (HMAC-SHA256, TTL court),
- transite tout son trafic via un **proxy nginx « relay-assistant »** qui authentifie chaque appel par paire `X-Relay-Client / X-Relay-Key`.

Le DM est une **source de vérité unique** : version courante, version cible, cohorte d'appartenance, accès (open / waitlist / Keycloak group), endpoint LLM, etc. Le plug-in n'a qu'à exposer son `client_uuid` + son `plugin_uuid` et à faire la conversation aux bons endpoints.

```
┌──────────────────┐  PKCE+token   ┌──────────────┐
│  Plug-in (host)  │──────────────▶│   Keycloak    │
└──────────────────┘               └──────────────┘
        │  bootstrap config / enroll / telemetry
        ▼
┌────────────────────────┐  X-Relay-Client/Key   ┌────────────────────────┐
│  relay-assistant nginx │──────────────────────▶│  device-management API │
│  (WAF-safe, auth_request) │                    │  (FastAPI + Postgres)   │
└────────────────────────┘                       └────────────────────────┘
        │
        ▼ (via relay)
   LLM, MCR, etc.
```

---

## 2. Principes généraux par plate-forme

| Plate-forme              | Format artefact | Manifest natif         | Hôte d'exécution                  | Mise à jour                                       | Statut MIrAI |
|---|---|---|---|---|---|
| **LibreOffice (Writer/Calc/Impress)** | `.oxt` (zip) | `description.xml`, `Addons.xcu`, `Jobs.xcu` | Python UNO bridge intégré | Auto via DM (dialogue natif `<update-information>`, repli `addExtension` in-process) | ✅ Production |
| **Chrome / Edge / Chromium** | `.crx` (zip signé) | `manifest.json` (MV3) | Service worker + content scripts | Chrome Web Store (notification DM seulement) | ✅ Production |
| **Firefox / Firefox ESR** | `.xpi` (zip signé) | `manifest.json` (MV3 partiel) + `browser_specific_settings.gecko` | Background scripts + content | AMO ou self-host (notification DM) | ✅ Production |
| **Thunderbird**         | `.xpi` (WebExtension)  | `manifest.json` + APIs `messenger.*` | Background script | AMO Thunderbird ou self-host | 🛠 Roadmap |
| **MS Office (Word/Excel/Outlook)** | Add-in Office (`.zip` + manifest XML) ou COM/VSTO | `manifest.xml` (Office Add-in) | WebView2 / .NET sandbox | AppSource ou centralized deployment via M365 admin | 🛠 Roadmap |
| **CLI / batch / IDE**   | quelconque | — | Process natif | DM peut piloter via mise à jour package OS | 🔬 Étude |

### Rappels MV2 vs MV3 (navigateur)

- **MV2** : background page persistante, `webRequest` synchrone, accès large à `chrome.*`. Déprécié par Google, encore supporté Firefox.
- **MV3** : service worker éphémère (réveil par event), `declarativeNetRequest` à la place de `webRequest` bloquant, `scripting.executeScript()` au lieu de `tabs.executeScript()`. Stockage `chrome.storage.local` toujours dispo. **C'est le format cible MIrAI.**
- Polyfill recommandé : `webextension-polyfill` ou abstraction maison (cf. `src/compat.js` du repo navigateur) pour mutualiser Chrome / Firefox.

### Spécificités UNO (LibreOffice)

- **Pas de pip** : seuls les modules de la stdlib Python (3.11, celle embarquée dans LibreOffice 25.8) sont disponibles. Toutes les requêtes HTTP passent par `urllib.request`.
- **Pas de threads UI** : `processEventsToIdle()`, `desktop.terminate()` ou tout dialog UNO **DOIT** être appelé depuis le main thread, sinon LO crash. Le plug-in LibreOffice utilise des callbacks postés via `XCallback` ou `threading.Timer` + flag class-level partagé entre instances (cf. [`entrypoint.py:353-355`](../src/mirai/entrypoint.py#L353-L355)).
- Toute UI passe par `com.sun.star.awt.*` (dialogs natifs), pas de Tk/Qt.

### Spécificités WebExtensions

- Le service worker MV3 peut être tué à tout moment ; **persister tout état** (`chrome.storage.local`) avant un `await fetch(...)`.
- `chrome.identity.launchWebAuthFlow()` ne supporte pas tous les redirect URIs : utiliser `chrome.identity.getRedirectURL()` ou un fallback onglet (cf. [`auth.js`](../../mirai-assistant-navigateur/src/auth.js)).
- Pas d'accès au filesystem ; pour les binaires (audio, etc.) utiliser `IndexedDB` ou `OPFS`.

---

## 3. Le Device Management — vue plug-in

### 3.1 Composants

| Composant | Rôle | Repo / chemin |
|---|---|---|
| **device-management** (FastAPI) | API publique + admin, base Postgres | `device-management/app/main.py` |
| **admin UI** | Création de plug-ins, campagnes, cohortes, artefacts | `device-management/app/admin/router.py` |
| **relay-assistant** (nginx) | Proxy WAF-safe vers Keycloak / LLM / MCR / telemetry, valide `X-Relay-*` via `auth_request` | `device-management/deploy/docker/relay-assistant.conf.template` |
| **Keycloak** | OIDC + PKCE, gestion des groupes (cohortes RBAC) | externe |
| **Postgres** | Stockage des relay-clients, campagnes, cohortes, artefacts | `device-management/db/schema.sql` |

### 3.2 Endpoints exposés au plug-in

Tous les chemins ci-dessous sont **derrière relay-assistant**. Le plug-in ne parle jamais directement à FastAPI en production.

| Étape | Méthode | Endpoint | Auth requis | Rôle |
|---|---|---|---|---|
| 1 — bootstrap | `GET` | `/config/{slug}/config.json?profile={prof}` | Aucun (public, secrets scrubbed) | Charger la config publique |
| 2 — login | `GET` | `/keycloak/protocol/openid-connect/auth` (PKCE) | — | OIDC code grant |
| 2bis | `POST` | `/keycloak/protocol/openid-connect/token` | — | Échange code → access_token |
| 3 — enroll | `POST` | `/enroll` | `Bearer {access_token}` | Émet `relay_client_id` + `relay_client_key` |
| 3bis (LO uniquement) | `POST` | `/enroll/confirm` | — | Vérif signature Ed25519 du challenge |
| 4 — config sécurisée | `GET` | `/config/{slug}/config.json?profile={prof}` | `X-Relay-Client/Key` | Config complète (avec secrets LLM) |
| 5 — telemetry token | `GET` | `/telemetry/token?device={slug}&profile={prof}` | `X-Relay-Client/Key` | Mint un Bearer HMAC court (5 min) |
| 6 — telemetry submit | `POST` | `/telemetry/v1/traces` | `Bearer {telemetry_token}` ou `X-Client-UUID` | Envoie traces OTLP |
| 7 — download artefact | `GET` | `/catalog/{slug}/download` | `X-Relay-Client/Key` | OXT/XPI/CRX, redirect 302 vers S3 présigné |
| 8 — status update | `POST` | `/update/status` | `X-Relay-Client/Key` | Reporte succès/échec installation |
| 9 — bind identité (opt.) | `POST` | `/identity/bind` | `Bearer {access_token}` + `X-Relay-*` | Lie le device à une identité utilisateur |

> Détails fichiers : [`app/main.py:2292`](../../device-management/app/main.py#L2292) (config), [`app/main.py:2463`](../../device-management/app/main.py#L2463) (enroll), [`app/main.py:2375`](../../device-management/app/main.py#L2375) (telemetry), [`app/main.py:2572`](../../device-management/app/main.py#L2572) (status).

---

## 4. Cycle de vie d'un plug-in

### 4.1 Vue séquentielle

```
[install]
    │
    ▼
[premier démarrage] ──▶ génère client_uuid (random) ──▶ persist storage
    │
    ▼
[bootstrap public] ──▶ GET /config/{slug}/config.json (sans header)
    │                  └─▶ bootstrap_url, keycloakIssuerUrl, telemetryEndpoint
    ▼
[wizard d'enrôlement]
    │
    ├─▶ User clique « Connexion »
    │      ▼
    │   [PKCE auth] code_verifier (32B random hex) → SHA-256 → b64url
    │      ▼ ouvre /keycloak/.../auth?code_challenge=...
    │      ▼ user authentifie → callback ?code=...
    │      ▼ POST /keycloak/.../token (code + verifier)
    │      ▼ access_token + refresh_token
    │
    ├─▶ POST /enroll (Bearer access_token, body: plugin_uuid, device_name, public_key)
    │      ▼ réponse: { relayClientId, relayClientKey, relayKeyExpiresAt }
    │      ▼ persist secure storage
    │
    └─▶ (LO uniquement) POST /enroll/confirm avec signature Ed25519(challenge)
           ▼ confirme la possession de la clé privée
    │
    ▼
[boucle nominale]
    │
    ├─▶ GET /config?profile=... avec X-Relay-Client/Key (refresh périodique 30 min)
    │      ▼ si update.action == "update" et target_version != current → planifier update
    │
    ├─▶ Sur chaque action utilisateur : appel LLM via /llm/... avec X-Relay-*
    │
    ├─▶ Périodiquement (≤ 5 min) : refresh telemetry_token via /telemetry/token
    │
    ├─▶ Async : POST /telemetry/v1/traces (batch OTLP)
    │
    └─▶ Si update planifié et user accepte : download → checksum → install → POST /update/status
```

### 4.2 Stratégie de rollout

Côté serveur, une campagne expose un tableau `rollout_config.stages` :

```json
[
  { "percent": 5,   "duration_hours": 24, "label": "canary" },
  { "percent": 25,  "duration_hours": 48, "label": "early_adopters" },
  { "percent": 100, "duration_hours": 0,  "label": "GA" }
]
```

**Éligibilité d'un device** = `MD5(client_uuid) % 100 < current_percent`. C'est **déterministe** : un même `client_uuid` reste dans le même palier pour toute la campagne. Le plug-in n'a rien à calculer — le DM répond simplement « oui voici une update » ou « rien pour toi ».

### 4.3 Cohortes

Quatre types : `manual` (liste UUID/email), `percentage` (échantillonnage), `email_pattern` (regex), `keycloak_group` (claim OIDC). Voir [`app/main.py:734`](../../device-management/app/main.py#L734).

---

## 5. Manifests embarqués dans l'artefact

Deux fichiers JSON doivent être présents à la racine (ou dans n'importe quel sous-répertoire) du `.oxt` / `.xpi` / `.crx`. Le DM les **extrait automatiquement à l'upload** et les supprime du paquet livré aux utilisateurs.

### 5.1 `dm-config.json` — template de configuration multi-profils

```json
{
  "configVersion": 1,
  "slug": "mon-plugin",
  "activeProfile": "prod",
  "default": {
    "llm_base_urls": "${{LLM_BASE_URL}}",
    "llm_default_models": "${{DEFAULT_MODEL_NAME}}",
    "llm_api_tokens": "${{LLM_API_TOKEN}}",
    "keycloakIssuerUrl": "${{KEYCLOAK_ISSUER_URL}}",
    "keycloakRealm": "${{KEYCLOAK_REALM}}",
    "keycloakClientId": "${{KEYCLOAK_CLIENT_ID}}",
    "telemetryEnabled": true,
    "telemetryEndpoint": "${{PUBLIC_BASE_URL}}/telemetry/v1/traces"
  },
  "dev":  { "llm_base_urls": "http://llm-dev.local:8000" },
  "int":  { "llm_base_urls": "https://llm-int.fake-domain.name" },
  "prod": { "llm_base_urls": "https://llm.fake-domain.name" }
}
```

- Les placeholders `${{VARNAME}}` sont **résolus côté serveur** au moment du `GET /config` à partir des variables d'environnement (`KEYCLOAK_ISSUER_URL`, etc.).
- Section `default` mergée d'abord, puis surcharge par profil.
- Stocké dans `plugins.config_template` (JSONB Postgres).

> Voir parsing : [`app/admin/router.py:68-130`](../../device-management/app/admin/router.py#L68).

### 5.2 `dm-manifest.json` — métadonnées + changelog

```json
{
  "slug": "mon-plugin",
  "name": "Mon Plug-in",
  "device_type": "libreoffice",
  "category": "productivity",
  "license": "MPL-2.0",
  "publisher": "Mon Org",
  "key_features": [
    "Action A",
    "Action B"
  ],
  "changelog": [
    {
      "version": "1.0.0",
      "date": "2026-04-19",
      "changes": [
        "Initial release"
      ]
    }
  ]
}
```

- `device_type` accepté : `libreoffice`, `chrome`, `firefox`, `thunderbird`, `office`.
- Stocké dans `plugins.changelog` (JSONB).
- Le `version` doit correspondre à celui du manifest natif (description.xml ou manifest.json) — côté LibreOffice, release-please met à jour `description.xml` et le build recopie la version dans `dm-manifest.json`.

---

## 6. Manifests de déploiement (DM côté serveur)

Quand un admin déploie un plug-in, le DM persiste un état complet. Voici un exemple concret de **payload de campagne** envoyé à `POST /api/plugins/{slug}/deploy` :

```json
{
  "version": "1.2.3",
  "artifact_path": "uploads/mon-plugin-1.2.3.oxt",
  "checksum_sha256": "ab12...",
  "min_host_version": "7.0",
  "max_host_version": null,
  "rollout": {
    "strategy": "canary",
    "stages": [
      { "percent": 5,   "duration_hours": 24 },
      { "percent": 25,  "duration_hours": 48 },
      { "percent": 100, "duration_hours": 0  }
    ],
    "target_cohort_id": null,
    "exclude_cohort_id": null
  },
  "notes": "Fix relay-assistant timeout on slow networks"
}
```

Et l'**update directive** que le plug-in reçoit dans la réponse `/config` :

```json
{
  "action": "update",
  "current_version": "1.2.2",
  "target_version": "1.2.3",
  "artifact_url": "/catalog/mon-plugin/download",
  "checksum": "ab12...",
  "urgency": "normal",
  "changelog_url": "https://...",
  "deadline_at": "2026-05-01T00:00:00Z",
  "campaign_id": 42
}
```

### 6.1 Modes d'accès (`plugins.access_mode`)

| Mode | Effet |
|---|---|
| `open` | Tout device qui appelle `/config` reçoit la config |
| `waitlist` | Liste blanche d'emails ; les autres reçoivent un message « contact admin » |
| `keycloak_group` | L'`access_token` doit contenir le claim `groups` incluant `required_group` |

---

## 7. Sécurité, authentification, topologie réseau

### 7.1 Modèle d'identité à 3 niveaux

| Niveau | Identifiant | Durée de vie | Usage |
|---|---|---|---|
| **Device** | `client_uuid` (UUIDv4 random) | Permanente, persistée localement ; conservée aux mises à jour, effacée à la désinstallation manuelle | Cohorte, campagne, dédup |
| **Plugin instance** | `plugin_uuid` | Permanente | Identification dans /enroll |
| **Relay credentials** | `relay_client_id` (`rc_xxx`) + `relay_client_key` (32B base64url) | TTL 30 jours, rotatable | Header `X-Relay-Client/Key` |
| **User session** | `access_token` (JWT Keycloak) | TTL court (~5 min) + refresh_token | OIDC pendant l'enroll |
| **Telemetry token** | HMAC-SHA256(`payload_b64.sig_b64`) | TTL 5 min | Bearer pour POST /telemetry |
| **Identity (opt.)** | Ed25519 keypair (LO) | Permanente côté plug-in | Signature challenge enroll/confirm |

### 7.2 PKCE — l'unique flow OIDC autorisé

- **code_verifier** : 32 octets random → hex (43-128 chars).
- **code_challenge** : `base64url(SHA-256(code_verifier))` sans padding.
- **scope** : `openid profile email`.
- **redirect_uri** :
  - LibreOffice : `http://localhost:{port}/callback` (localhost ou 127.0.0.1, `entrypoint.py` `_wait_for_auth_code`) où le port est celui de `keycloak_redirect_uri` (28443 par convention), à libérer avant tout test SSO (`lsof -nP -iTCP:28443`).
  - Navigateur : `chrome.identity.getRedirectURL()` ou onglet `callback.html` (plus compatible password managers).
  - Office Add-in : Office Dialog API `displayDialogAsync()` + redirect URI déclaré dans le manifest.

### 7.3 Topologie réseau (relay-assistant)

```
Plug-in ──HTTPS──▶ relay-assistant (nginx) ──HTTP──▶ device-management:3001
                          │                       └──▶ keycloak:8080
                          │                       └──▶ llm-backend:8000
                          │                       └──▶ mcr:5000
                          │
                          └─ auth_request /__relay_auth ──▶ /relay/authorize
                                                            │
                                                            └─ verify X-Relay-Client/Key
```

- Tous les sous-chemins (`/keycloak/...`, `/llm/...`, `/mcr-api/...`, `/telemetry/...`) sont protégés par `auth_request`.
- Resolver DNS dans le namespace K8s : `10.32.0.10` (kube-dns ClusterIP).
- Côté plug-in, **l'unique base URL à connaître** est `bootstrap_url` (le reste se déduit).

### 7.4 Rotation des relay credentials

- `relay_client_key` n'est **renvoyée qu'une fois**, à l'enrôlement (le serveur en stocke un hash SHA-256 salé/peppered).
- Le plug-in peut demander une rotation en re-appelant `/enroll` avec un `Bearer` valide.
- À expiration (`relayKeyExpiresAt < now`), le plug-in **doit** ré-enrôler — sinon tous les appels retournent `401`.

---

## 8. Échanges réseau et précautions WAF

> ⚠️ **À lire avant tout commit réseau.** Les WAFs en production (DGX, intranet ministériel) sont stricts.

### 8.1 Les 3 règles d'or

1. **Tout corps de requête DOIT être JSON `application/json`.** Pas de `multipart/form-data` au-delà de quelques KB. Pour uploader un binaire > 768 KB, utiliser l'API chunked `POST /api/upload-chunk` (cf. [`router.py:1412`](../../device-management/app/admin/router.py#L1412)).
2. **Tout binaire dans un payload JSON DOIT être encodé en base64url sans padding.** C'est la convention universelle MIrAI : signatures, clés publiques, nonces, icônes, blobs audio courts. Le helper Python `_b64e()` ([`security_flow.py:18`](../src/mirai/security_flow.py#L18)) encode `urlsafe_b64encode + rstrip("=")` ; côté JS utiliser :
   ```js
   const b64url = btoa(String.fromCharCode(...new Uint8Array(buf)))
     .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
   ```
3. **Le `User-Agent` DOIT identifier le plug-in**, pas un navigateur. Format : `MIrAI-{Platform}/{version} {HostApp}/{hostVersion}`. Spoofer un UA Chrome **déclenche le WAF** (anti-bot rules). Cf. [`entrypoint.py`](../src/mirai/entrypoint.py) (chercher `_with_user_agent`).

### 8.2 Pourquoi JSON+b64 partout

Les WAFs analysent les corps `multipart/*` byte par byte (anti-malware) et bloquent ceux > 2 MB. Un payload JSON avec un champ `"signature": "Hk7p..."` passe trivialement. Le **double encapsulage** est volontaire :
- couche 1 : sérialisation JSON (`_json_bytes(payload)`),
- couche 2 : b64url des champs binaires.

### 8.3 Signatures et timestamps

Pour les endpoints `/enroll/confirm`, `/identity/bind`, `/telemetry/token` (refresh), le payload signé suit ce schéma :

```python
payload = {
  "plugin_uuid": "...",
  "enroll_id":   "...",
  "public_key":  _b64e(public_raw),       # b64url sans padding
  "nonce":       _b64e(os.urandom(16)),   # 16 octets random
  "timestamp":   int(time.time()),         # epoch seconds
}
payload["signature"] = _b64e(ed25519_sign(json_bytes(payload)))
```

Le serveur vérifie : (1) `timestamp` dans une fenêtre ±5 min, (2) `nonce` non vu (anti-replay), (3) signature Ed25519 valide vs `public_key` enregistrée à l'enroll.

### 8.4 Headers communs à émettre sur tout appel DM

| Header | Origine | Toujours requis ? |
|---|---|---|
| `Content-Type: application/json` | constante | sur POST/PUT |
| `User-Agent: MIrAI-{Platform}/{ver}` | constante | oui |
| `X-Client-UUID` | storage local | oui (pré-enroll surtout) |
| `X-Plugin-Version` | manifest natif | oui |
| `X-Platform-Type` | constante (`libreoffice`, `chrome`, …) | oui |
| `X-Platform-Version` | host introspectable | recommandé |
| `X-Relay-Client` | enrollment | dès qu'enrôlé |
| `X-Relay-Key` | enrollment | dès qu'enrôlé |
| `Authorization: Bearer …` | PKCE ou telemetry mint | sur `/enroll`, `/telemetry/v1/traces` |

---

## 9. Exemples de code clés

### 9.1 Génération du `client_uuid` (JS, MV3)

```javascript
// background.js — service worker startup
chrome.runtime.onInstalled.addListener(async () => {
  const { dmClientUUID } = await chrome.storage.local.get('dmClientUUID');
  if (!dmClientUUID) {
    await chrome.storage.local.set({ dmClientUUID: crypto.randomUUID() });
  }
});
```

Côté Python (LibreOffice) : `uuid.uuid4()` à la première exécution, persisté dans `<profil LibreOffice>/user/config/mirai/settings.json` (clés `extensionUUID` / `plugin_uuid`). Une souche d'identité `user/config/config.json` est conservée à côté, pour que l'identité de l'appareil soit conservée si l'extension est ramenée à une version antérieure (par exemple par une campagne de retour arrière du DM). L'identité survit aux mises à jour et n'est effacée qu'à la désinstallation manuelle depuis le Gestionnaire des extensions.

### 9.2 PKCE — challenge + verifier (JS)

```javascript
function _generateCodeVerifier() {
  const arr = new Uint8Array(32);
  crypto.getRandomValues(arr);
  return Array.from(arr, b => b.toString(16).padStart(2, '0')).join('');
}

async function _generateCodeChallenge(verifier) {
  const data = new TextEncoder().encode(verifier);
  const digest = await crypto.subtle.digest('SHA-256', data);
  return btoa(String.fromCharCode(...new Uint8Array(digest)))
    .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}
```

### 9.3 Enrôlement (JS, navigateur)

```javascript
async function enrollInDM(accessToken, bootstrapUrl, dmClientUUID, pluginVersion) {
  const resp = await fetch(`${bootstrapUrl}/enroll`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${accessToken}`,
      'User-Agent': `MIrAI-Browser/${pluginVersion}`,
      'X-Client-UUID': dmClientUUID,
      'X-Plugin-Version': pluginVersion,
      'X-Platform-Type': 'chrome'
    },
    body: JSON.stringify({
      device_name: 'mirai-browser',
      plugin_uuid: dmClientUUID,
      email: ''
    })
  });
  if (!resp.ok) throw new Error(`enroll failed: ${resp.status}`);
  const data = await resp.json();
  await chrome.storage.local.set({
    dmEnrollment: {
      enrolled: true,
      enrolledAt: Date.now(),
      relayClientId: data.relayClientId,
      relayClientKey: data.relayClientKey,
      relayKeyExpiresAt: data.relayKeyExpiresAt
    }
  });
}
```

### 9.4 Enrôlement avec signature Ed25519 (Python, LibreOffice)

```python
import json, base64, urllib.request, os, time
from nacl.signing import SigningKey  # ou cryptography si nacl indispo

def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

def _json_bytes(payload: dict) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")

def enroll_anonymous(bootstrap_url, plugin_uuid, device_name, signing_key, access_token):
    public_raw = signing_key.verify_key.encode()
    payload = {
        "plugin_uuid": plugin_uuid,
        "device_name": device_name,
        "public_key": _b64e(public_raw),
    }
    body = _json_bytes(payload)
    req = urllib.request.Request(
        f"{bootstrap_url}/enroll",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": "MIrAI-LibreOffice/1.0.0 LibreOffice/7.5",
            "Authorization": f"Bearer {access_token}",
            "X-Client-UUID": plugin_uuid,
            "X-Plugin-Version": "1.0.0",
            "X-Platform-Type": "libreoffice",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        enroll_resp = json.loads(resp.read())

    enroll_id = enroll_resp.get("enroll_id") or enroll_resp.get("id")
    challenge = enroll_resp.get("challenge")

    # Étape 2 : prouver la possession de la clé privée
    if enroll_id and challenge:
        challenge_bytes = base64.urlsafe_b64decode(challenge + "==")
        signature = signing_key.sign(challenge_bytes).signature
        confirm_payload = {
            "enroll_id": enroll_id,
            "plugin_uuid": plugin_uuid,
            "signature": _b64e(signature),
            "public_key": _b64e(public_raw),
        }
        urllib.request.urlopen(
            urllib.request.Request(
                f"{bootstrap_url}/enroll/confirm",
                data=_json_bytes(confirm_payload),
                method="POST",
                headers={"Content-Type": "application/json"},
            ),
            timeout=10,
        )
    return enroll_resp  # contient relayClientId, relayClientKey
```

> Référence complète : [`src/mirai/security_flow.py:537-592`](../src/mirai/security_flow.py#L537-L592).

### 9.5 Appel relay-authentifié (config refresh)

```python
def fetch_config(bootstrap_url, slug, profile, relay_id, relay_key, plugin_uuid):
    url = f"{bootstrap_url}/config/{slug}/config.json?profile={profile}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "MIrAI-LibreOffice/1.0.0 LibreOffice/7.5",
        "X-Client-UUID": plugin_uuid,
        "X-Relay-Client": relay_id,
        "X-Relay-Key": relay_key,
    })
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())
```

### 9.6 Telemetry token + envoi OTLP (JS)

```javascript
async function getTelemetryToken(bootstrapUrl, slug, profile, enrollment) {
  const resp = await fetch(
    `${bootstrapUrl}/telemetry/token?device=${slug}&profile=${profile}`,
    { headers: {
        'X-Relay-Client': enrollment.relayClientId,
        'X-Relay-Key':    enrollment.relayClientKey,
    }}
  );
  return await resp.json();  // { telemetryKey, telemetryKeyExpiresAt, ... }
}

async function sendTrace(bootstrapUrl, telemetryToken, otlpJson) {
  await fetch(`${bootstrapUrl}/telemetry/v1/traces`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${telemetryToken}`,
    },
    body: JSON.stringify(otlpJson),  // OTLP-JSON (resourceSpans[]…)
  });
}
```

### 9.7 Téléchargement + vérif checksum d'une mise à jour (Python)

```python
import hashlib

def download_and_verify(bootstrap_url, slug, expected_sha256, relay_id, relay_key):
    req = urllib.request.Request(
        f"{bootstrap_url}/catalog/{slug}/download",
        headers={
            "X-Relay-Client": relay_id,
            "X-Relay-Key": relay_key,
        },
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = resp.read()
    actual = hashlib.sha256(data).hexdigest()
    if actual.lower() != expected_sha256.lower():
        raise RuntimeError(f"checksum mismatch: {actual} != {expected_sha256}")
    return data
```

---

## 10. Checklist : créer un nouveau plug-in en 1 jour

- [ ] **Choisir un slug** unique (ex. `mirai-mon-truc`), `device_type` (`libreoffice` / `chrome` / …).
- [ ] **Bootstrapper l'arbo** native (`description.xml` + `Addons.xcu` pour LO, `manifest.json` MV3 pour navigateur).
- [ ] **Embarquer `dm-config.json`** avec un profil `default` + un profil `dev` pointant vers ton DM local Docker Compose.
- [ ] **Embarquer `dm-manifest.json`** avec slug, name, version, changelog initial.
- [ ] **Implémenter le client UUID** (random, persisté).
- [ ] **Implémenter le bootstrap config public** (`GET /config/{slug}/config.json?profile=dev`, sans header).
- [ ] **Implémenter PKCE** vers Keycloak (code_verifier hex 32 octets, code_challenge SHA-256/b64url).
- [ ] **Implémenter `POST /enroll`** avec `Authorization: Bearer {access_token}` + headers `X-Client-UUID`, `X-Plugin-Version`, `X-Platform-Type`.
- [ ] **Ranger les secrets selon leur durée de vie** : identifiants durables (`relayClientId/relayClientKey`, refresh token) dans le coffre de l'OS (Gestionnaire d'identification Windows, Keychain macOS ; mémoire seule là où il n'y en a pas, Linux) ou `chrome.storage.local` pour un plug-in navigateur ; jetons courts (`llmToken`, clé de télémétrie, `access_token` Keycloak) en mémoire, jamais dans un fichier. Implémentation de référence LibreOffice : `src/mirai/credentials.py` ; Thunderbird utilise `nsILoginManager`.
- [ ] **Refactor toutes les requêtes** vers DM/LLM pour ajouter `X-Relay-Client/Key`.
- [ ] **Implémenter le refresh config** périodique (alarm 30 min, ou timer Python).
- [ ] **Détecter `update.action == "update"`** dans la réponse config et planifier l'update (manuel ou auto selon plate-forme).
- [ ] **Implémenter telemetry** : mint token toutes les ~5 min, batch OTLP-JSON, POST.
- [ ] **Tester l'enroll** contre un DM local : `cd device-management/deploy/docker && docker compose up`.
- [ ] **Vérifier User-Agent** : ne **jamais** spoofer un navigateur ; utiliser `MIrAI-{Platform}/{version}`.
- [ ] **Demander à l'admin DM** de créer le plug-in dans l'admin UI : `/admin/catalog/create` avec ton slug + premier upload `.oxt/.xpi/.crx`.
- [ ] **Premier déploiement canary** : `POST /api/plugins/{slug}/deploy` avec `strategy=canary, stages=[{5,24},{25,48},{100,0}]`.

---

## 11. Annexes

### 11.1 Variables d'environnement DM côté serveur (extrait)

| Variable | Rôle | Exemple |
|---|---|---|
| `KEYCLOAK_ISSUER_URL` | URL de base Keycloak (sans `/realms/...`) | `https://<HOTE_INTERNE>/relay-assistant/keycloak` |
| `KEYCLOAK_REALM` | Nom du realm | `mirai` |
| `KEYCLOAK_CLIENT_ID` | Client OIDC du plug-in | `mirai-libreoffice` |
| `LLM_BASE_URL` | Endpoint OpenAI-compatible | `https://api.scaleway.ai/v1` |
| `LLM_API_TOKEN` | Token Bearer LLM | (secret) |
| `PUBLIC_BASE_URL` | URL publique du DM (pour résoudre les placeholders) | `https://bootstrap.fake-domain.name` |
| `DM_RELAY_KEY_TTL_SECONDS` | TTL des relay credentials | `2592000` (30 j) |
| `DM_TELEMETRY_TOKEN_TTL_SECONDS` | TTL des Bearer telemetry | `300` (5 min) |
| `DM_TELEMETRY_TOKEN_SIGNING_KEY` | Secret HMAC | (secret) |
| `DM_QUEUE_ADMIN_TOKEN` | `X-Admin-Token` API admin | (secret) |

### 11.2 Référence rapide des fichiers à connaître

| Question | Fichier de référence |
|---|---|
| Comment fonctionne l'enroll Python ? | [`src/mirai/security_flow.py:537`](../src/mirai/security_flow.py#L537) |
| Comment fonctionne l'enroll JS ? | [`mirai-assistant-navigateur/src/auth.js`](../../mirai-assistant-navigateur/src/auth.js) |
| Comment le DM mint les telemetry tokens ? | [`device-management/app/main.py:616`](../../device-management/app/main.py#L616) |
| Comment le DM construit l'update directive ? | [`device-management/app/main.py:959`](../../device-management/app/main.py#L959) |
| Comment relay-assistant valide les requêtes ? | [`device-management/deploy/docker/relay-assistant.conf.template`](../../device-management/deploy/docker/relay-assistant.conf.template) |
| Schéma DB complet | [`device-management/db/schema.sql`](../../device-management/db/schema.sql) |
| Format admin de création de plug-in | [`device-management/app/admin/router.py:1754`](../../device-management/app/admin/router.py#L1754) |
| Tableau des stratégies de rollout | [`docs/DEPLOY.md`](DEPLOY.md) |
| Spec télémétrie OTLP | [`docs/TELEMETRY.md`](TELEMETRY.md) |

### 11.3 FAQ courte

**Q. Mon plug-in doit-il signer ses requêtes ?**
R. Non, sauf `/enroll/confirm` et `/identity/bind` (Ed25519). Les autres appels sont authentifiés par les headers `X-Relay-Client/Key`.

**Q. Que faire si `relay_client_key` est compromise ?**
R. L'admin peut révoquer la paire dans l'UI DM (table `relay_clients`, colonne `revoked_at`). Le plug-in ré-enrôlera automatiquement au prochain `401`.

**Q. Mon plug-in fonctionne offline. Que faire ?**
R. Cacher la dernière config valide dans le storage local + utiliser un profil `local-llm` qui désactive le bootstrap (`enabled: false`, `bootstrap_urls: []`). Cf. profil [`config/profiles/`](../config/profiles/).

**Q. Comment tester sans déployer ?**
R. `cd ../device-management/deploy/docker && docker compose up` lance un DM local complet (FastAPI + Postgres + Keycloak + relay-assistant). Utiliser le profil `config/profiles/config.default.dev.json` (`bootstrap_urls`) qui pointe sur `http://localhost:8089`.

**Q. Mon WAF bloque mes requêtes — pourquoi ?**
R. Vérifier (1) `User-Agent` (pas de spoof navigateur), (2) `Content-Type: application/json` strict, (3) corps < 2 MB ou utiliser chunked, (4) tous les binaires en base64url dans des champs JSON.

---

**Maintenance** : ce guide doit être mis à jour quand un nouvel endpoint DM est exposé, quand une convention de header change, ou quand une nouvelle plate-forme est ajoutée. Garder les liens fichiers cohérents avec les chemins réels.
