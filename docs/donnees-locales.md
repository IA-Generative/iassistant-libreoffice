# Données locales de l'extension MIrAI

Document destiné à la DSI et au support : ce que l'extension écrit sur le poste, où, et comment tout effacer.

## Où

Tout vit dans un seul dossier du profil LibreOffice : `<profil LibreOffice>/user/config/mirai/`.

| Système | Dossier |
|---|---|
| Windows | `%APPDATA%\LibreOffice\4\user\config\mirai` |
| Linux | `~/.config/libreoffice/4/user/config/mirai` |
| macOS | `~/Library/Application Support/LibreOffice/4/user/config/mirai` |

| Fichier | Contenu |
|---|---|
| `settings.json` | Préférences, état local, identité de l'appareil (`extensionUUID`, `plugin_uuid`), drapeau `enrolled` |
| `dm_snapshot.json` | Dernière réponse `/config` du Device Management, secrets vidés |
| `install_state.json` | Version installée, dossier du paquet, empreinte du transport (environnement visé par l'OXT) |
| `assistant_conversation.json` | Historique de la conversation de l'assistant |
| `prompts_calc.txt`, `prompt.txt` | Invites personnalisées |
| `secure_bootstrap_state.json` | État de la télémétrie sécurisée |
| `telemetry_queue.json` | File d'attente de la télémétrie |
| `pending_update/` | Mise à jour téléchargée en attente d'installation |
| `mirai.log`, `mirai.log.1` | Journal (voir plus bas) |

Un ancien fichier `user/config/config.json` (à côté du dossier `mirai/`) subsiste : c'est une souche `{extensionUUID, plugin_uuid}` conservée pour que l'identité de l'appareil survive si l'extension est ramenée à une version plus ancienne (par exemple par une campagne de retour arrière du Device Management). Elle est aussi écrite sur une installation neuve.

## Secrets

Aucun secret n'est écrit dans ces fichiers, à une exception près : le flux de télémétrie sécurisée hérité, quand il est actif, garde un jeton porteur de télémétrie dans `secure_bootstrap_state.json`. Il n'est actif que si le Python de LibreOffice dispose du paquet `cryptography` : ce n'est pas le cas du Python embarqué sous Windows et macOS ; sous Linux, LibreOffice utilise le Python du système, où ce paquet peut être installé.

- Les jetons de courte durée (`llmToken` du DM, `telemetryKey`, `access_token` Keycloak) ne vivent qu'en mémoire du processus.
- Les secrets de longue durée vont dans le coffre du système :
  - **Windows** : Gestionnaire d'identification, entrées génériques `MIrAI-LibreOffice/relay_client_id`, `MIrAI-LibreOffice/relay_client_key`, `MIrAI-LibreOffice/relay_key_expires_at`, `MIrAI-LibreOffice/refresh_token`, `MIrAI-LibreOffice/proxy_password`, `MIrAI-LibreOffice/llm_api_tokens`. Persistance « entreprise » (suit le profil itinérant), avec repli sur « ordinateur local ».
  - **macOS** : trousseau, service `MIrAI-LibreOffice`, compte = nom de la clé (mêmes six noms).
  - **Linux** : mémoire seulement. L'assistant d'enrôlement se rejoue à chaque session LibreOffice.
- Ce que le coffre apporte : les secrets sont chiffrés au repos par le système et restent hors du profil LibreOffice (une copie du profil n'en emporte aucun). Il ne les cache pas aux autres programmes de la même session : tout processus de l'utilisateur peut lire un identifiant générique du Gestionnaire d'identification ; sous macOS, un élément créé par l'outil `security` reste lisible sans confirmation par tout programme de l'utilisateur qui passe par ce même outil.
- Si le coffre refuse une écriture, le secret est gardé en mémoire pour la session.
- La paire relais et le `refresh_token` sont liés à l'environnement visé : l'ensemble des adresses DM de l'OXT (`bootstrap_urls` et `bootstrap_url`, sans égard à l'ordre, à la casse de l'hôte ni à la barre finale) et son `config_path` (donc le `?profile=`). `enabled` et `bootstrap_insecure_urls` n'en font pas partie ; ajouter ou retirer une adresse de repli change d'environnement.

## Mise à jour

Tout est conservé ; seuls les caches dérivés sont recalculés. Un OXT construit pour un autre environnement (autres adresses DM ou autre profil) fait écarter puis effacer, au démarrage suivant, les identifiants de l'ancien environnement : nouvel enrôlement. Exception : la première mise à niveau depuis l'ancienne disposition (`user/config/config.json`) reprend la paire relais de ce fichier sous l'environnement du nouvel OXT ; si elle avait été émise par un autre DM, celui-ci la refuse et il faut se ré-enrôler.

## Désinstallation

- **Depuis le Gestionnaire des extensions** : en une dizaine de secondes au plus (deux constats espacés de 3 s), tout est effacé : dossier `mirai/`, souche `config.json`, entrées du coffre, mémoire, identifiant d'appareil compris. Toutes les écritures sont ensuite gelées jusqu'à la fin de la session. Laisser LibreOffice ouvert quelques secondes, puis le redémarrer. Désactiver ou mettre à jour l'extension n'efface rien.
- **Hors LibreOffice** (`unopkg remove`, outil de déploiement) : rien ne peut s'exécuter côté extension, la désinstallation n'est pas détectée. Fermer LibreOffice dans les secondes qui suivent une désinstallation manuelle peut aussi empêcher l'effacement. Dans ces cas, lancer le nettoyage ci-dessous.

## Nettoyage DSI (Windows, PowerShell, session de l'utilisateur)

```powershell
Remove-Item -Recurse -Force "$env:APPDATA\LibreOffice\4\user\config\mirai" -ErrorAction SilentlyContinue
Remove-Item -Force "$env:APPDATA\LibreOffice\4\user\config\config.json" -ErrorAction SilentlyContinue
foreach ($n in "relay_client_id","relay_client_key","relay_key_expires_at","refresh_token","proxy_password","llm_api_tokens") {
    cmdkey /delete:"MIrAI-LibreOffice/$n" 2>$null | Out-Null
}
```

À confirmer sur un poste : que `cmdkey /delete:<cible>` supprime bien des identifiants de type générique. Contrôle : `cmdkey /list | findstr MIrAI-LibreOffice` ne doit plus rien renvoyer. Sinon, utiliser la forme `LegacyGeneric` :

```powershell
foreach ($n in "relay_client_id","relay_client_key","relay_key_expires_at","refresh_token","proxy_password","llm_api_tokens") {
    cmdkey /delete:"LegacyGeneric:target=MIrAI-LibreOffice/$n" 2>$null | Out-Null
}
```

Les anciennes versions écrivaient aussi dans `user/config/` (`config_cache.json`, `assistant_conversation.json`, `prompts_calc.txt`, `prompt.txt`, `telemetry_queue.json`, `secure_bootstrap_state.json`, `pending_update/`) et dans `log.txt` à la racine du dossier personnel ; ces fichiers peuvent être supprimés sur les postes concernés.

## Journal

`mirai/mirai.log`, avec `mirai.log.1` pour la rotation : 1 Mo maximum chacun. Le journal ne contient ni jeton, ni réponse `/config` brute du DM, ni corps de requête envoyé au modèle, ni texte du document : les lignes du journal d'actions de l'assistant n'y laissent que leur taille, l'extrait lui-même n'apparaît que dans l'onglet « Actions ».
