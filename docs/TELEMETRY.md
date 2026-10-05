# Documentation de la télémétrie OpenTelemetry

## Vue d'ensemble

L'extension mirai intègre OpenTelemetry (Tempo) pour collecter des traces anonymisées sur l'utilisation des fonctionnalités. Cette documentation décrit l'implémentation technique et la configuration.

## Architecture

### Composants

1. **Génération d'UUID** : Au premier lancement, un UUID unique est généré et stocké dans la configuration
2. **Envoi de traces** : Chaque action utilisateur génère une trace OpenTelemetry
3. **Format OTLP/JSON** : Les traces sont envoyées au format JSON compatible OTLP
4. **Authentification** : Support de Basic et Bearer authentication

### Fonctions principales

#### `generate_trace_id()`
Génère un trace ID hexadécimal de 32 caractères (16 bytes).

#### `generate_span_id()`
Génère un span ID hexadécimal de 16 caractères (8 bytes).

#### `send_telemetry_trace(config, span_name, attributes=None)`
Envoie une trace OpenTelemetry complète à l'endpoint configuré.

**Paramètres :**
- `config` : Objet de configuration (instance de MainJob)
- `span_name` : Nom de l'action (ex: "ExtendSelection")
- `attributes` : Dictionnaire d'attributs supplémentaires

**Exemple d'appel :**
```python
send_telemetry_trace(self, "EditSelection", {
    "action": "edit_selection",
    "text_length": str(len(text_range.getString()))
})
```

## Événements tracés

### ExtensionLoaded
Envoyé au chargement de l'extension dans LibreOffice.

**Attributs :**
- `event.type`: "extension_loaded"
- `extension.context`: "libreoffice_writer"

### ExtendSelection
Envoyé lors de l'utilisation de la génération de texte (CTRL+Q).

**Attributs :**
- `action`: "extend_selection"
- `text_length`: Longueur du texte sélectionné

### EditSelection
Envoyé lors de l'utilisation de la modification de texte (CTRL+E).

**Attributs :**
- `action`: "edit_selection"
- `text_length`: Longueur du texte sélectionné

### SummarizeSelection
Envoyé lors de l'utilisation du résumé (CTRL+R).

**Attributs :**
- `action`: "summarize_selection"
- `text_length`: Longueur du texte sélectionné

### SimplifySelection
Envoyé lors de l'utilisation de la reformulation (CTRL+L).

**Attributs :**
- `action`: "simplify_selection"
- `text_length`: Longueur du texte sélectionné

### OpenmiraiWebsite
Envoyé lors de l'accès au site web mirai.

**Attributs :**
- `action`: "open_website"

### OpenSettings
Envoyé lors de l'ouverture du dialogue des paramètres.

**Attributs :**
- `action`: "open_settings"

## Format des traces

### Structure OTLP/JSON

```json
{
  "resourceSpans": [
    {
      "resource": {
        "attributes": [
          {"key": "service.name", "value": {"stringValue": "mirai-libreoffice"}},
          {"key": "service.version", "value": {"stringValue": "1.0.0"}},
          {"key": "extension.uuid", "value": {"stringValue": "UUID-UNIQUE"}}
        ]
      },
      "scopeSpans": [
        {
          "scope": {
            "name": "mirai-extension",
            "version": "1.0.0"
          },
          "spans": [
            {
              "traceId": "32-char-hex-trace-id",
              "spanId": "16-char-hex-span-id",
              "name": "ExtendSelection",
              "kind": 1,
              "startTimeUnixNano": "1700000000000000000",
              "endTimeUnixNano": "1700000000001000000",
              "attributes": [
                {"key": "extension.uuid", "value": {"stringValue": "UUID"}},
                {"key": "extension.name", "value": {"stringValue": "mirai"}},
                {"key": "extension.version", "value": {"stringValue": "1.0.0"}},
                {"key": "action", "value": {"stringValue": "extend_selection"}},
                {"key": "text_length", "value": {"stringValue": "150"}}
              ],
              "status": {"code": 1}
            }
          ]
        }
      ]
    }
  ]
}
```

## Configuration

### Paramètres disponibles

Dans `mirai.json` :

```json
{
  "telemetryEnabled": true,
  "telemetryEndpoint": "https://traces.cpin.numerique-interieur.com/v1/traces",
  "telemetrySel": "mirai_salt",
  "telemetryAuthorizationType": "Basic",
  "telemetryKey": "dGVzdC1lcmljOnRlc3QtZXJpYw==",
  "telemetryHost": "",
  "telemetrylogJson": false,
  "telemetryFormatProtobuf": false,
  "extensionUUID": ""
}
```

### Valeurs par défaut

Les valeurs par défaut sont définies dans la méthode `_get_telemetry_defaults()` :

```python
{
    "telemetryEnabled": True,
    "telemetryEndpoint": "https://traces.cpin.numerique-interieur.com/v1/traces",
    "telemetrySel": "mirai_salt",
    "telemetryAuthorizationType": "Basic",
    "telemetryKey": "dGVzdC1lcmljOnRlc3QtZXJpYw==",
    "telemetryHost": "",
    "telemetrylogJson": False,
    "telemetryFormatProtobuf": False
}
```

### Authentification

#### Basic Authentication
```python
telemetryAuthorizationType: "Basic"
telemetryKey: "base64(username:password)"
```

Exemple :
```bash
echo -n "test-eric:test-eric" | base64
# Résultat : dGVzdC1lcmljOnRlc3QtZXJpYw==
```

#### Bearer Token
```python
telemetryAuthorizationType: "Bearer"
telemetryKey: "votre-token-jwt"
```

## Sécurité et confidentialité

### Données collectées

✅ **Collectées :**
- UUID anonyme de l'extension
- Nom des actions effectuées
- Longueur des textes traités
- Timestamps des actions
- Métadonnées techniques (trace ID, span ID)

❌ **NON collectées :**
- Contenu des textes
- Informations personnelles
- Données sensibles
- Prompts utilisateur

### SSL/TLS

Les requêtes vers l'endpoint de télémétrie utilisent SSL/TLS. Pour les environnements de développement, la vérification des certificats peut être désactivée :

```python
ssl_context = ssl.create_default_context()
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE
```

⚠️ **Note :** En production, il est recommandé d'activer la vérification des certificats.

## Désactivation

Pour désactiver complètement la télémétrie :

1. **Via la configuration :**
```json
{
  "telemetryEnabled": false
}
```

2. **Via suppression du fichier de configuration :**
Si aucun fichier `mirai.json` n'existe, la télémétrie est activée par défaut avec les paramètres par défaut.

## Dépannage

### Activer les logs détaillés

```json
{
  "telemetrylogJson": true
}
```

Les logs seront écrits dans `mirai/mirai.log` avec le payload JSON complet des traces.

### Vérifier les traces envoyées

Les logs incluent le statut de la réponse HTTP :
```
Telemetry trace sent successfully: ExtendSelection, status: 200
```

### Erreurs courantes

#### Timeout de connexion
```
Error sending telemetry trace: <urlopen error [Errno 60] Operation timed out>
```
Solution : Vérifier l'accès réseau à l'endpoint de télémétrie.

#### Erreur d'authentification
```
HTTP Error 401: Unauthorized
```
Solution : Vérifier la clé d'authentification `telemetryKey`.

#### Endpoint introuvable
```
HTTP Error 404: Not Found
```
Solution : Vérifier l'URL de `telemetryEndpoint`.

## Intégration avec Tempo

L'extension envoie des traces au format OTLP/JSON compatible avec Grafana Tempo. Pour visualiser les traces :

1. Accéder à Grafana
2. Sélectionner la source de données Tempo
3. Rechercher par `service.name="mirai-libreoffice"`
4. Filtrer par `extension.uuid` pour suivre une instance spécifique

### Exemple de requête TraceQL

```traceql
{service.name="mirai-libreoffice" && resource.extension.uuid="VOTRE-UUID"}
```

## Évolutions futures

- [ ] Support du format Protobuf (OTLP/Proto)
- [ ] Métriques en plus des traces
- [ ] Logs structurés OpenTelemetry
- [ ] Configuration via variables d'environnement
- [ ] Sampling configurable
- [ ] Batching des traces
