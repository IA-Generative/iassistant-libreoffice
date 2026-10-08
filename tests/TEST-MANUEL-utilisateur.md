# Recette manuelle utilisateur — Login / sécurité / overlay

> **Pourquoi ce document ?** Le build et les smoke tests sont automatisés
> (`scripts/smoke-deploy.sh` dans chaque repo). Mais certaines vérifications
> exigent un **humain dans une vraie session** : login SSO interactif,
> persistance de session entre redémarrages, overlay sur une vraie page de
> visio, inspection du stockage des credentials. Cette recette couvre ces
> points. Le même fichier est présent dans les deux repos visés
> (IAssistant-Direct = extension, AssistantMiraiLibreOffice = plugin).
>
> Cocher `[x]` chaque étape. Noter version testée + navigateur/OS en bas.

- **Préalable automatisé (doit déjà être vert)** :
  - Extension : `scripts/smoke-deploy.sh` → Jest vert + `dist/extension/` produit.
  - Plugin : `scripts/smoke-deploy.sh` → pytest vert + `.oxt` installé via unopkg.

---

## A. Extension navigateur (IAssistant-Direct)

### A.1 — Chargement
- [ ] Chrome : `chrome://extensions` → mode développeur → **Charger l'extension non empaquetée** → `dist/extension/`. L'icône MirAI apparaît.
- [ ] Firefox : `about:debugging#/runtime/this-firefox` → **Charger un module temporaire** → le `.xpi`. L'icône apparaît.
- [ ] Aucune erreur dans la console du service worker (`chrome://extensions` → service worker → Inspecter).

### A.2 — Login SSO (PKCE)
- [ ] Clic sur l'icône → **Se connecter** : un onglet Keycloak s'ouvre, login ministériel OK.
- [ ] Après login, le popup affiche l'état connecté (pas de redemande immédiate).

### A.3 — Persistance de session (⇽ point 2 : « redemande à chaque fois »)
- [ ] **Fermer puis rouvrir le popup** plusieurs fois → **pas** de nouvelle demande de login.
- [ ] **Redémarrer le navigateur** → rouvrir le popup → toujours connecté (refresh silencieux).
- [ ] Laisser l'access token expirer (selon TTL Keycloak) puis agir → reconnexion **silencieuse**, sans onglet de login.
- [ ] Couper le réseau quelques secondes pendant une action → au retour, **pas** de login interactif intempestif (échec transitoire absorbé).

### A.4 — Overlay visio (⇽ point 3 : « code visio demandé alors qu'il n'y en a pas »)
- [ ] Ouvrir une réunion **`visio.numerique.gouv.fr`** → l'overlay (pastille REC) s'affiche.
- [ ] Cliquer **REC** : l'enregistrement démarre **sans** message « Identifiant requis » ni champ mot de passe. *(après implémentation du plan : `comu` seul exige identifiant + mot de passe)*
- [ ] Même test sur **`webconf.numerique.gouv.fr`** et **`webinaire.numerique.gouv.fr`** → démarrage direct.
- [ ] Sur **`comu`** : l'overlay demande bien identifiant + mot de passe, et l'enregistrement démarre une fois saisis.

### A.5 — Nettoyage plateformes (⇽ point 4) — *après implémentation*
- [ ] Ouvrir une page **Google Meet** et **Teams** → **aucun** overlay MirAI ne s'injecte.
- [ ] Le sélecteur de plateforme du popup ne propose plus **gmeet/teams/webex**.

### A.6 — Stockage des credentials (⇽ point 1) — *après implémentation du durcissement*
> DevTools de l'extension → Application → Storage → Extension storage (`chrome.storage.local`).
- [ ] `miraiToken` est **chiffré** (objet `{iv, salt, ciphertext}`), pas de JWT en clair.
- [ ] La clé en clair **`miraiTokenKey` est ABSENTE** (remplacée par une CryptoKey non-extractible en IndexedDB).
- [ ] `visioCredentials` n'est **pas en clair** (chiffré).
- [ ] Plus aucune entrée `encryptedCreds` (legacy à clé statique supprimé).

---

## B. Plugin LibreOffice (AssistantMiraiLibreOffice)

### B.1 — Installation
- [ ] `scripts/smoke-deploy.sh` a installé l'extension (ou `unopkg list` montre `fr.gouv.interieur.mirai`).
- [ ] Lancer LibreOffice Writer → la barre d'outils / le menu MirAI est présent.

### B.2 — Login + persistance de session (⇽ points 1 & 2)
- [ ] Première action MirAI → login SSO dans le navigateur → retour OK dans LibreOffice.
- [ ] **Fermer puis relancer LibreOffice** → une action MirAI ne **redemande pas** de login (refresh via refresh/offline token).
- [ ] Laisser expirer l'access token → action → reconnexion silencieuse.

### B.3 — Stockage des secrets (⇽ point 1) — *après implémentation du durcissement*
> Fichier : `~/Library/Application Support/LibreOffice/4/user/config/config.json` (macOS).
- [ ] `config.json` ne contient **plus** en clair : `access_token`, `refresh_token`, `relay_client_key`, `llm_api_tokens`, `telemetryKey`.
- [ ] Ces secrets sont dans le coffre OS : `security find-generic-password -s mirai-libreoffice` (macOS) en retourne.
- [ ] Permissions de `config.json` = `600` (`stat -f '%Sp' config.json`).
- [ ] Migration : partir d'un ancien `config.json` avec tokens en clair → après 1 lancement, ils sont déplacés dans le coffre et **effacés** du fichier.

### B.4 — Actions de base (non-régression)
- [ ] Writer : sélectionner du texte → action MirAI (résumé / correction…) → résultat inséré.
- [ ] Calc : génération de formule / analyse → résultat OK.

---

## C. Résultat

| Champ | Valeur |
|------|--------|
| Date |  |
| Testeur |  |
| Version extension |  |
| Version plugin (.oxt) |  |
| Navigateur / OS |  |
| Verdict (OK / KO + n° d'étape) |  |

> Toute étape KO : noter le n° (ex. A.3) + capture/console et remonter à l'équipe.
