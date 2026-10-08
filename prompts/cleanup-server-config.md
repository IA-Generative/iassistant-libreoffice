# Prompt — Nettoyage des configs serveur device-management

> Ce prompt nettoie les fichiers de configuration côté device-management
> pour retirer les clés obsolètes et ajouter les clés manquantes,
> suite au nettoyage du plugin (commit 11c5481).

---

## Repo cible

`/Users/etiquet/Documents/GitHub/device-management`

## État actuel

Le bootstrap sert des clés que le plugin n'utilise plus, et il manque
des clés utilisées par le plugin dans certains profils.

### Clés à SUPPRIMER (plus lues par le plugin)

| Clé | Raison |
|-----|--------|
| `api_type` | Toujours "chat", hardcodé dans le plugin |
| `is_openwebui` | Supprimé du plugin (était hardcodé True) |
| `openai_compatibility` | Supprimé du plugin |
| `model` | Placeholder "achanger-model", jamais lu (le plugin utilise `llm_default_models`) |
| `proxy_consistency_checked_once` | Le dialogue proxy au démarrage a été supprimé |
| `proxy_allow_insecure_ssl` | À garder uniquement si le serveur a besoin de le piloter — sinon supprimer |
| `_comment_device_management` | Commentaire, ignoré par le code |
| `_auth_notice` | Message informatif, jamais lu par le plugin |
| `access_token` | Toujours vide — le vrai token est obtenu via PKCE et stocké localement |
| `refresh_token` | Toujours vide — idem, géré localement après l'auth |
| `enrolled` | Toujours false — le plugin gère son statut d'enrôlement localement |

### Clés à AJOUTER dans le profil prod (présentes dans int mais pas prod)

| Clé | Valeur |
|-----|--------|
| `doc_url` | `"https://github.com/IA-Generative/AssistantMiraiLibreOffice/blob/master/docs/notice-utilisateur.md"` |
| `portal_url` | `"https://mirai.interieur.gouv.fr"` |
| `telemetrylogJson` | `true` |

### Clés à garder telles quelles ✅

```
llm_base_urls, llm_default_models, llm_api_tokens,
authHeaderName, authHeaderPrefix,
keycloakIssuerUrl, keycloakRealm, keycloakClientId,
keycloak_redirect_uri, keycloak_allowed_redirect_uri,
systemPrompt,
extend_selection_max_tokens, extend_selection_system_prompt,
edit_selection_max_new_tokens, edit_selection_system_prompt,
summarize_selection_max_tokens, summarize_selection_system_prompt,
simplify_selection_max_tokens, simplify_selection_system_prompt,
analyze_range_max_tokens, llm_request_timeout_seconds,
doc_url, portal_url,
enabled, bootstrap_url, config_path, device_name,
telemetryEnabled, telemetryEndpoint, telemetryKey,
telemetryAuthorizationType, telemetrySel, telemetrylogJson,
relayAssistantBaseUrl
```

Note : `telemetryKeyExpiresAt` et `telemetryKeyTtlSeconds` sont générées
dynamiquement par le serveur — ne pas toucher.

---

## Fichiers à modifier

### 1. Config files — `config/libreoffice/` (ou `config/mirai-libreoffice/` si déjà renommé)

Modifier les 3 fichiers :
- `config.json` (prod)
- `config.dev.json` (dev)
- `config.int.json` (int)

Dans chacun, supprimer de l'objet `config` :
```json
"model": "achanger-model",
"api_type": "chat",
"is_openwebui": true,
"openai_compatibility": false,
"proxy_allow_insecure_ssl": true,
"proxy_consistency_checked_once": false,
"_comment_device_management": "Device Management (configuration centralisee)",
"access_token": "",
"refresh_token": "",
"enrolled": false,
```

Dans `config.json` (prod) uniquement, ajouter :
```json
"doc_url": "https://github.com/IA-Generative/AssistantMiraiLibreOffice/blob/master/docs/notice-utilisateur.md",
"portal_url": "https://mirai.interieur.gouv.fr",
"telemetrylogJson": true,
```

### 2. Configmap K8s — `deploy/k8s/base/manifests/10-configmap-device-management.yaml`

Appliquer les mêmes suppressions/ajouts dans les blocs :
- `libreoffice-config.json` (~ligne 78)
- `libreoffice-config-int.json` (~ligne 125)

Aussi supprimer si présents :
```
"telemetrySel": "...",
"telemetryHost": "",
"telemetryFormatProtobuf": false,
"obfuscated_telemetry_key": "..."
```

### 3. Config int spécifique

Dans `config.int.json`, supprimer aussi ces clés qui ne sont pas dans les autres profils :
```json
"telemetryHost": "",
"telemetryFormatProtobuf": false,
"obfuscated_telemetry_key": "<base64(user:pass)>"
```

---

## Vérification après modification

### Test local (device-management tourne sur localhost:3001)

```bash
# Vérifier que les clés supprimées n'apparaissent plus
curl -s "http://localhost:3001/config/mirai-libreoffice/config.json?profile=dev" | python3 -c "
import sys, json
d = json.load(sys.stdin)['config']
removed = ['api_type', 'is_openwebui', 'openai_compatibility', 'model',
           'proxy_consistency_checked_once', '_comment_device_management',
           'telemetryHost', 'telemetryFormatProtobuf', 'obfuscated_telemetry_key']
errors = [k for k in removed if k in d]
if errors:
    print(f'ERREUR: clés non supprimées: {errors}')
else:
    print('OK: toutes les clés obsolètes supprimées')

required = ['doc_url', 'portal_url', 'telemetrylogJson',
            'analyze_range_max_tokens', 'llm_request_timeout_seconds']
missing = [k for k in required if k not in d]
if missing:
    print(f'ERREUR: clés manquantes: {missing}')
else:
    print('OK: toutes les clés requises présentes')
"
```

### Test Scaleway

```bash
# Après redéploiement K8s
./scripts/k8s/deploy.sh scaleway
kubectl rollout restart deployment/device-management -n bootstrap

# Vérifier prod
curl -s "https://bootstrap.fake-domain.name/config/mirai-libreoffice/config.json?profile=prod" | python3 -c "
import sys, json
d = json.load(sys.stdin)['config']
assert 'api_type' not in d, 'api_type should be removed'
assert 'is_openwebui' not in d, 'is_openwebui should be removed'
assert d.get('doc_url'), 'doc_url missing'
assert d.get('portal_url'), 'portal_url missing'
assert d.get('analyze_range_max_tokens') == 4000, 'analyze_range_max_tokens wrong'
print('OK: config prod nettoyée')
"
```

### Test plugin E2E

```bash
cd /Users/etiquet/Documents/GitHub/AssistantMiraiLibreOffice
rm -f ~/Library/Application\ Support/LibreOffice/4/user/config/config.json
scripts/dev-launch.sh --config config/profiles/config.default.integration.json
# Vérifier dans ~/log.txt :
# - Pas de warning sur des clés manquantes
# - Les fonctions Writer et Calc fonctionnent normalement
```

---

## Config cible finale (ce que le bootstrap doit servir)

```json
{
  "config": {
    "llm_base_urls": "${{LLM_BASE_URL}}",
    "llm_default_models": "${{DEFAULT_MODEL_NAME}}",
    "llm_api_tokens": "${{LLM_API_TOKEN}}",
    "authHeaderName": "Authorization",
    "authHeaderPrefix": "Bearer ",
    "keycloakIssuerUrl": "${{KEYCLOAK_ISSUER_URL}}",
    "keycloakRealm": "${{KEYCLOAK_REALM}}",
    "keycloakClientId": "${{KEYCLOAK_CLIENT_ID}}",
    "keycloak_redirect_uri": "${{KEYCLOAK_REDIRECT_URI}}",
    "keycloak_allowed_redirect_uri": "${{KEYCLOAK_ALLOWED_REDIRECT_URI}}",
    "portal_url": "https://mirai.interieur.gouv.fr",
    "doc_url": "https://github.com/IA-Generative/AssistantMiraiLibreOffice/blob/master/docs/notice-utilisateur.md",
    "systemPrompt": "...",
    "extend_selection_max_tokens": 15000,
    "extend_selection_system_prompt": "",
    "edit_selection_max_new_tokens": 15000,
    "edit_selection_system_prompt": "",
    "summarize_selection_max_tokens": 15000,
    "summarize_selection_system_prompt": "",
    "simplify_selection_max_tokens": 15000,
    "simplify_selection_system_prompt": "",
    "analyze_range_max_tokens": 4000,
    "llm_request_timeout_seconds": 45,
    "enabled": true,
    "bootstrap_url": "${{PUBLIC_BASE_URL}}/",
    "config_path": "/config/mirai-libreoffice/config.json",
    "device_name": "mirai-libreoffice",
    "telemetryEnabled": true,
    "telemetryEndpoint": "${{PUBLIC_BASE_URL}}/telemetry/v1/traces",
    "telemetryAuthorizationType": "Bearer",
    "telemetryKey": "${{TELEMETRY_KEY}}",
    "telemetrySel": "${{TELEMETRY_SALT}}",
    "telemetrylogJson": true,
    "relayAssistantBaseUrl": "${{PUBLIC_BASE_URL}}/relay-assistant"
  }
}
```
