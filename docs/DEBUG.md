# Guide de débogage pour mirai

## 1. Fichier de log automatique

Le code génère automatiquement des logs dans `<profil LibreOffice>/user/config/mirai/mirai.log` (rotation `mirai.log.1`, 1 Mo chacun).

### Voir les logs en temps réel :

```bash
tail -f ~/.config/libreoffice/4/user/config/mirai/mirai.log
```

### Effacer les logs :

```bash
rm ~/.config/libreoffice/4/user/config/mirai/mirai.log*
```

### Informations loguées :

- URL de l'endpoint
- Type d'API (chat/completions)
- Modèle utilisé
- Headers HTTP (jeton masqué)
- Taille de la requête, jamais son corps ni le texte du document
- Statut de la réponse
- Erreurs éventuelles

## 2. Console LibreOffice (macOS)

### Lancer LibreOffice en mode console :

```bash
/Applications/LibreOffice.app/Contents/MacOS/soffice --writer
```

Les erreurs Python apparaîtront dans le terminal.

## 3. Débogage manuel avec des messages

Vous pouvez ajouter temporairement des affichages dans le document pour déboguer :

```python
text_range.setString(text_range.getString() + f"\nDEBUG: {variable_to_check}")
```

## 4. Tester l'API manuellement

### Test avec curl (OpenWebUI) :

```bash
curl -X POST http://localhost:3000/api/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "llama2",
    "messages": [{"role": "user", "content": "Hello"}],
    "stream": true
  }'
```

### Test avec curl (OpenAI) :

```bash
curl -X POST https://api.openai.com/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -d '{
    "model": "gpt-3.5-turbo",
    "messages": [{"role": "user", "content": "Hello"}],
    "stream": true
  }'
```

## 5. Vérifier la configuration

Les paramètres sont stockés dans :
```
~/Library/Application Support/LibreOffice/4/user/mirai.json
```

### Voir la configuration actuelle :

```bash
cat ~/Library/Application\ Support/LibreOffice/4/user/mirai.json
```

### Exemple de configuration pour OpenWebUI :

```json
{
    "endpoint": "http://localhost:3000",
    "model": "llama2",
    "api_key": "",
    "api_type": "chat",
    "is_openwebui": true,
    "extend_selection_max_tokens": 70,
    "extend_selection_system_prompt": "",
    "edit_selection_max_new_tokens": 0,
    "edit_selection_system_prompt": ""
}
```

### Exemple de configuration pour OpenAI :

```json
{
    "endpoint": "https://api.openai.com",
    "model": "gpt-3.5-turbo",
    "api_key": "sk-...",
    "api_type": "chat",
    "is_openwebui": false,
    "extend_selection_max_tokens": 70
}
```

## 6. Erreurs courantes

### HTTP Error 405: Method Not Allowed
- Vérifiez que le bon type d'API est configuré (chat vs completions)
- Pour OpenWebUI, assurez-vous que `is_openwebui` est à `true`
- Testez l'URL avec curl pour confirmer le bon endpoint

### SSL: CERTIFICATE_VERIFY_FAILED
- Le code désactive maintenant la vérification SSL par défaut
- Si le problème persiste, vérifiez votre connexion réseau

### Pas de réponse / Timeout
- Vérifiez que le serveur est accessible : `curl http://localhost:3000`
- Vérifiez les logs : `tail -f ~/.config/libreoffice/4/user/config/mirai/mirai.log`
- Assurez-vous que le modèle existe sur votre serveur

### L'extension ne s'affiche pas dans le menu
- Redémarrez LibreOffice complètement
- Réinstallez l'extension : Outils → Gestionnaire des extensions

## 7. Clean install (purge complète)

Le script `00-clean-install.sh` remet le plugin dans l'état d'une installation fraîche :

- Ferme LibreOffice
- Efface les données locales : dossier `config/mirai/`, souche `config.json`, fichiers des anciennes versions, entrées du trousseau macOS (voir `docs/donnees-locales.md`)
- Supprime les fichiers de logs LibreOffice (`unopkg.log`, `GraphicsRenderTests.log`)
- Purge le cache temp des extensions (`extensions/tmp/`)

```bash
# Reset simple (extension conservée — pour tester un ré-enrôlement)
./scripts/00-clean-install.sh

# Reset complet + désinstalle l'extension
./scripts/00-clean-install.sh --uninstall

# Ensuite, réinstaller et relancer
./scripts/dev-launch.sh
```

Options disponibles :

| Option | Description |
|---|---|
| `--uninstall` | Désinstalle aussi l'extension Mirai |

### Réinstaller manuellement

```bash
/Applications/LibreOffice.app/Contents/MacOS/unopkg remove fr.gouv.interieur.mirai
/Applications/LibreOffice.app/Contents/MacOS/unopkg add dist/mirai.oxt
```

Puis redémarrez LibreOffice.

## 8. Mode développement

Pour modifier et tester rapidement :

```bash
cd /Users/etiquet/Documents/GitHub/mirai

# Modifier le code
nano main.py

# Recréer le package
rm -f mirai.oxt && \
zip -r mirai.oxt Accelerators.xcu Addons.xcu description.xml main.py META-INF/ registration/ assets/

# Réinstaller
unopkg remove org.extension.sample
unopkg add mirai.oxt

# Relancer LibreOffice
```
