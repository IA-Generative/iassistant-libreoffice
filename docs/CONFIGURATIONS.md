# Les deux configurations de travail

> Deux tiers permettent de faire tourner le plugin de bout en bout : un **DM local en Docker
> avec Ollama** (développement hors ligne, pas de coût, pas de dépendance réseau) et le
> **DM Scaleway** (l'existant, partagé). Ce document décrit les deux, la bascule dans les
> deux sens, et ce qu'il faut vérifier à chaque fois.
>
> **Aucun secret, aucun jeton, aucune URL interne réelle dans ce fichier.** Tout ce qui
> ressemble à `<QUELQUE_CHOSE>` est un placeholder à remplacer localement.
>
> État de vérification : les deux tiers ont été exercés le **2026-07-26** ; ce qui est
> marqué ✅ a été observé, pas supposé.

## Vue d'ensemble

| | **DM local (Docker + Ollama)** | **DM Scaleway** |
|---|---|---|
| Usage | développement, démo hors ligne, tests destructifs | intégration et production |
| Base bootstrap | `http://localhost:8089` | `https://<BOOTSTRAP_HOST>` |
| Profil DM | `?profile=dev` | `?profile=prod` |
| Profil plugin | `config/profiles/config.default.dev.json` | `config.default.production.json` / `config.default.kubernetes.json` (à créer depuis les `.example`, gitignorés) |
| Backend LLM | Ollama sur l'hôte (`llama3.2:latest`) | passerelle LLM du ministère |
| SSO Keycloak | absent par défaut (voir « mode dev sans SSO ») | Keycloak réel, flux PKCE complet |
| Coût d'un appel | nul | facturé |
| Purge / remise à zéro | `docker compose down -v` | impossible — ne jamais tester de scénarios destructifs |

---

## Tier 1 — DM local en Docker + Ollama

### Prérequis

- Docker Desktop en marche.
- Ollama installé avec un modèle léger :
  ```bash
  ollama serve                     # doit répondre sur http://localhost:11434
  ollama pull llama3.2             # ~2 Go, suffisant pour le développement
  curl -s http://localhost:11434/v1/models   # ✅ doit lister llama3.2:latest
  ```
- Le dépôt voisin `../device-management` présent, avec `deploy/docker/.env` et
  `deploy/docker/.env.secrets` (les copier depuis les `.example` au premier usage).

### Architecture réelle de la pile

Cinq services. **Deux chemins distincts mènent au LLM** — c'est le point qui fait perdre
le plus de temps :

| Service | Port hôte | Rôle | Variable d'amont LLM |
|---|---|---|---|
| `device-management` | **8089** | `/config`, `/enroll`, `/telemetry`, `/update` | — |
| `llm-proxy` | 8090 | **sert `/llm/v1` au plugin** (mode proxy DM) | **`LLM_BASE_URL`** |
| `relay-assistant` (nginx) | 8088 | chemin relais historique | `RELAY_LLM_UPSTREAM` |
| `queue-worker` | — | traitement asynchrone | — |
| `postgres-local` | 5433 | base (profil `postgres-local`) | — |

> ⚠️ Le plugin en mode proxy DM appelle `/llm/v1` : la variable qui compte est donc
> **`LLM_BASE_URL`** (service `llm-proxy`), pas seulement `RELAY_LLM_UPSTREAM`.
> Régler les deux évite toute ambiguïté.

### Trois pièges de démarrage — vérifiés

1. **Conflit de nom de réseau.** Le compose déclare `telemetry: {external: true, name: docker_default}` ;
   lancé depuis `deploy/docker/`, le projet compose s'appelle « docker » et veut créer un
   réseau *default* du même nom → `network docker_default was found but has incorrect label`.
   **Parade : imposer un nom de projet** (`-p mirai-dm-local`), et créer une fois pour toutes
   les réseaux externes attendus.
2. **`DATABASE_ADMIN_URL` n'est pas surchargeable par l'environnement.** Il n'apparaît pas dans
   le bloc `environment:` du compose : il n'arrive que par `env_file: .env`. Une variable de
   shell est donc **ignorée**, et le conteneur meurt sur
   `could not translate host name "postgres"`. **Parade : un fichier d'override compose**
   (le bloc `environment:` est prioritaire sur `env_file`).
3. **`/enroll` exige un jeton porteur même quand la vérification est désactivée.**
   `DM_AUTH_VERIFY_ACCESS_TOKEN=false` ne dispense pas de l'en-tête : le code lit l'e-mail
   dans le jeton et refuse en 401 si le résultat est vide.

### Fichier d'override

`deploy/docker/docker-compose.ollama.yml` (à créer localement, **ne pas committer** s'il
contient des valeurs propres au poste) :

```yaml
services:
  device-management:
    environment:
      DATABASE_URL: postgresql://dev:dev@postgres-local:5432/bootstrap
      DATABASE_ADMIN_URL: postgresql://dev:dev@postgres-local:5432/bootstrap
      LLM_BASE_URL: http://host.docker.internal:11434/v1
      LLM_API_TOKEN: ollama-local-noauth
      DEFAULT_MODEL_NAME: llama3.2:latest
      # Dev local sans Keycloak. NE JAMAIS activer ailleurs.
      DM_AUTH_VERIFY_ACCESS_TOKEN: "false"
  queue-worker:
    environment:
      DATABASE_URL: postgresql://dev:dev@postgres-local:5432/bootstrap
      DATABASE_ADMIN_URL: postgresql://dev:dev@postgres-local:5432/bootstrap
  llm-proxy:
    environment:
      DATABASE_URL: postgresql://dev:dev@postgres-local:5432/bootstrap
      DATABASE_ADMIN_URL: postgresql://dev:dev@postgres-local:5432/bootstrap
      LLM_BASE_URL: http://host.docker.internal:11434/v1
      LLM_API_TOKEN: ollama-local-noauth
      DEFAULT_MODEL_NAME: llama3.2:latest
  relay-assistant:
    environment:
      RELAY_LLM_UPSTREAM: http://host.docker.internal:11434/v1
```

### Démarrage

```bash
cd ../device-management/deploy/docker

# Une seule fois : les réseaux déclarés externes par le compose
docker network create owui-net    2>/dev/null || true
docker network create docker_default 2>/dev/null || true

docker compose -p mirai-dm-local \
  -f docker-compose.yml -f docker-compose.ollama.yml \
  --profile postgres-local up -d --build
```

### Vérifications ✅

```bash
# 1. Le DM répond et sert bien le contrat d'auth /llm/v1
curl -s "http://localhost:8089/config/mirai-libreoffice/config.json?profile=dev"
#    ✅ HTTP 200, et le bloc "config" contient llmEndpoint, llmToken,
#       llmTokenExpiresAt, embdUrl  (llmToken vide tant qu'on n'est pas enrôlé)

# 2. Sans identifiants, /llm/v1 refuse — message attendu, ce n'est pas une panne
curl -s http://localhost:8089/llm/v1/models
#    ✅ HTTP 401 "Missing credentials: provide X-Relay-Client/X-Relay-Key headers
#       or Authorization: Bearer <llmToken>"
```

### Chaîne complète, sans le plugin ✅

Utile pour distinguer un problème de serveur d'un problème de plugin.

```bash
# Jeton de dev NON SIGNÉ — accepté uniquement parce que
# DM_AUTH_VERIFY_ACCESS_TOKEN=false. Jamais hors du poste de dev.
TOK=$(python3 -c "
import base64,json
b=lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip('=')
print(b({'alg':'none','typ':'JWT'})+'.'+b({'email':'dev@local.test','sub':'dev-local'})+'.sig')")

# 1) Enrôlement → paire relais (credential maître, 30 j)
curl -s -X POST http://localhost:8089/enroll \
  -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' \
  -d '{"device_type":"libreoffice","device_name":"dev-poste",
       "plugin_uuid":"<UUID>","client_uuid":"<UUID>"}'
#    ✅ {"ok":true, "relayClientId":"rc_…", "relayClientKey":"…", "relayKeyExpiresAt":…}

# 2) /config AVEC les en-têtes relais → mint du llmToken (scopé llm, TTL 1 h)
curl -s "http://localhost:8089/config/mirai-libreoffice/config.json?profile=dev" \
  -H "X-Relay-Client: <RELAY_CLIENT_ID>" -H "X-Relay-Key: <RELAY_CLIENT_KEY>"
#    ✅ config.llmToken non vide, et llm_api_tokens = la MÊME valeur

# 3) Appel LLM réel sur Ollama, avec le llmToken SEUL
curl -s http://localhost:8089/llm/v1/chat/completions \
  -H "Authorization: Bearer <LLM_TOKEN>" -H 'Content-Type: application/json' \
  -d '{"model":"llama3.2:latest","max_tokens":20,
       "messages":[{"role":"user","content":"Réponds exactement: QUALIF-OK"}]}'
#    ✅ HTTP 200, choices[0].message.content = "QUALIF-OK !",
#       et un bloc "usage" (prompt_tokens/completion_tokens/total_tokens)
```

> Le bloc `usage` est renvoyé spontanément par ce chemin : la jauge de jetons peut
> afficher une valeur exacte sans jamais ajouter `stream_options` à la requête.

**Règle d'or de l'en-tête** — sur `/llm/v1`, envoyer **le `llmToken` seul**. Dès que
`X-Relay-Client` est présent, le DM bascule sur la branche relais et échoue en 401
**sans repli** vers le Bearer. La paire relais reste réservée à `/config`, `/telemetry`
et `/update/status`.

### Basculer le plugin sur le tier local

```bash
cd <racine du dépôt>
./scripts/06-use-config-profile.sh --profile dev      # bootstrap http://localhost:8089, ?profile=dev
./scripts/00-clean-install.sh --uninstall
./scripts/02-build-oxt.sh
/Applications/LibreOffice.app/Contents/MacOS/unopkg add --force --suppress-license \
  "$PWD/dist/mirai.oxt"
```

Puis, dans LibreOffice, dérouler un **vrai** enrôlement.

### Télémétrie locale

Le point d'entrée `/telemetry/v1/traces` exige un jeton porteur de télémétrie **ou** un
en-tête `X-Client-UUID` — les en-têtes `X-Relay-*` seuls ne suffisent pas
(`401 "Missing telemetry Bearer token or X-Client-UUID header."`, vérifié).

### Arrêt

```bash
docker compose -p mirai-dm-local -f docker-compose.yml -f docker-compose.ollama.yml \
  --profile postgres-local down          # -v en plus pour repartir d'une base vide
```

---

## Tier 2 — DM Scaleway

### Configuration en place

| | |
|---|---|
| Base bootstrap | `https://<BOOTSTRAP_HOST>` (Kapsule Scaleway) |
| Profil DM | `?profile=prod` |
| Profils plugin | `config/profiles/config.default.production.json`, `config.default.kubernetes.json` (à créer depuis les `.example`, gitignorés) |
| SSO | Keycloak réel, flux PKCE, callback `http://localhost:28443/callback` |
| Déploiement | `cd ../device-management && ./scripts/k8s/deploy.sh scaleway` |
| Dépôt de référence | **`device-management-private`** pour l'environnement `prod-sdid` |

Disponibilité vérifiée le 2026-07-26 : ✅ `/config?profile=prod` → **HTTP 200 en 0,25 s** ;
`/llm/v1/models` sans identifiants → **HTTP 401** (comportement attendu).

### Bascule vers Scaleway

```bash
cd <racine du dépôt>
cp config/profiles/config.default.production.example.json config/profiles/config.default.production.json   # renseigner bootstrap_urls
./scripts/06-use-config-profile.sh --profile production
./scripts/00-clean-install.sh --uninstall
./scripts/02-build-oxt.sh
/Applications/LibreOffice.app/Contents/MacOS/unopkg add --force --suppress-license \
  "$PWD/dist/mirai.oxt"
```

### Précautions — chacune a déjà coûté une session de débogage

1. **Changer de tier = installer un OXT construit pour ce tier** : les identifiants de
   l'ancien environnement sont écartés automatiquement au démarrage (ré-enrôlement).
   Une paire relais émise par le DM local est refusée par Scaleway, et inversement.
   Exception : la première mise à niveau depuis l'ancienne disposition (`config.json` à la
   racine de `user/config/`) reprend la paire de ce fichier sous l'environnement du nouvel
   OXT ; si elle vient d'un autre DM, elle est refusée (401) jusqu'à un ré-enrôlement.
   **Règle de livraison** : changer dans un OXT l'ensemble des URL DM (`bootstrap_urls`,
   `bootstrap_url` — ajouter ou retirer une URL de repli compte) ou le `?profile=` de
   `config_path` revient à planifier une vague de ré-enrôlement : au démarrage suivant, les
   identifiants de l'ancien environnement sont effacés sur chaque poste mis à jour. L'ordre
   des URL, `enabled` et `bootstrap_insecure_urls` n'entrent pas en compte.
2. **Ne jamais poser `enrolled: true` à la main** dans
   `~/Library/Application Support/LibreOffice/4/user/config/mirai/settings.json`. Le drapeau franchit
   le gate mais aucune paire relais n'existe : 401 « Missing credentials » sur 100 % des
   appels, **et le drapeau empêche le ré-enrôlement qui corrigerait la situation**
   (`[ENROLL] Auto-check: already enrolled, skipping wizard`). Seule sortie : un vrai
   ré-enrôlement — `POST /enroll` est idempotent côté DM (il révoque puis ré-émet).
3. **Libérer le port 28443 avant tout test SSO** : `lsof -nP -iTCP:28443`. Un simulateur lié
   à `127.0.0.1:28443` intercepte le callback sans que le plugin échoue — il attend 3 min
   puis expire. Signature du diagnostic : **aucune ligne `PKCE callback received` dans
   `mirai/mirai.log`** alors que le navigateur a bien affiché une page.
4. **Neutraliser la campagne de mise à jour pendant les tests d'authentification** : une
   directive d'update ouvre une boîte modale à chaque lancement et **interrompt le login SSO
   en cours**. Aligner la version du build sur la cible de campagne (`target == current`
   ⇒ directive sautée) ou désactiver la campagne côté DM.
5. **Un seul onglet SSO à la fois** : le serveur de callback ne connaît que le `state` du
   dernier flux ; fermer les anciens onglets avant de recommencer.

### Suivi d'une campagne

```bash
curl -s -H "X-Admin-Token: $DM_ADMIN_TOKEN" \
  https://<BOOTSTRAP_HOST>/api/campaigns/<ID>/progress | python3 -m json.tool
```

---

## Diagnostic — par où commencer

| Symptôme | Première chose à regarder |
|---|---|
| 401 sur tous les appels IA | `mirai/settings.json` (drapeau `enrolled`) et coffre de l'OS : y a-t-il **`relay_client_id` ET `relay_client_key` ET un `llm_api_tokens` non vide** ? Si `enrolled: true` sans paire relais → état absorbant, ré-enrôlement obligatoire. |
| « Callback invalide (state inconnu) » | `lsof -nP -iTCP:28443`. Ce message **n'existe pas dans le code du plugin** : s'il s'affiche, il vient d'un autre processus. |
| Une boîte modale coupe le login SSO | Campagne de mise à jour active : aligner la version ou désactiver la campagne. |
| Le plugin semble ne rien faire | `mirai/mirai.log` : chercher le span de l'action (`SummarizeSelection`…) puis la réponse HTTP. Si les deux sont présents, l'action a bien tourné — c'est l'affichage qui est en cause. |
| `unopkg` échoue en `NoConnectException` | Launch constraints macOS. Contrôle : `/Applications/LibreOffice.app/Contents/Resources/python --version` doit renvoyer **0**, pas 137 ; sinon re-signer ad hoc (`codesign --force -s -`). |
| Le conteneur DM redémarre en boucle | `docker logs <conteneur>` : presque toujours `DATABASE_ADMIN_URL`, non surchargeable autrement que par un override compose. |

Le journal applicatif du plugin est **`<profil LibreOffice>/user/config/mirai/mirai.log`** — c'est la source de vérité, le code y
trace abondamment.
