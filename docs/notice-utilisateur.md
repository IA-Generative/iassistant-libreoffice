# MIrAI — Notice utilisateur

Extension LibreOffice intégrant un assistant IA directement dans **Writer** et **Calc**.

---

## Sommaire rapide par profil

| 🟢 Novice | 🔵 Expert |
|-----------|-----------|
| [Ce que MIrAI peut faire](#1-ce-que-mirai-peut-faire-pour-vous) | [Ce que MIrAI peut faire](#1-ce-que-mirai-peut-faire-pour-vous) |
| [Installer l'extension (GUI)](#22-installer-lextension) | [Installation CLI et profils](#22-installer-lextension) |
| [Premier lancement](#23-premier-lancement) | [Premier lancement](#23-premier-lancement) |
| [Fonctionnalités Writer](#3-fonctionnalités-writer) | [Fonctionnalités Writer](#3-fonctionnalités-writer) |
| [Fonctionnalités Calc](#4-fonctionnalités-calc) | [Fonctionnalités Calc](#4-fonctionnalités-calc) |
| [Paramètres (menu)](#51--via-le-menu-mirai--paramètres) | [Configuration avancée](#52--fichiers-de-configuration-et-profils) |
| [Bonnes pratiques](#6-bonnes-pratiques) | [Proxy et SSL](#53--proxy-et-certificats-ssl) |
| [Résolution de problèmes](#7-résolution-de-problèmes) | [Personnaliser les prompts](#54--personnaliser-les-prompts-système) |
| [Raccourcis clavier](#8-raccourcis-clavier-aide-mémoire) | [Résolution de problèmes](#7-résolution-de-problèmes) |
| | [Raccourcis clavier](#8-raccourcis-clavier-aide-mémoire) |

---

## 1. Ce que MIrAI peut faire pour vous

MIrAI est un assistant d'écriture intégré à LibreOffice. Il vous aide à rédiger, reformuler, résumer et corriger vos documents sans quitter votre traitement de texte. Vous sélectionnez du texte, vous choisissez une action dans le menu MIrAI (ou via un raccourci clavier), et l'IA vous propose un résultat en quelques secondes.

Dans **Writer**, vous pouvez par exemple : continuer un brouillon, reformuler un paragraphe en langage simple, résumer un long rapport, ou ajuster la longueur d'un passage. Dans **Calc**, vous pouvez transformer une colonne entière (traduire, classifier…), générer des formules en langage naturel ou analyser des tendances dans vos données.

Toutes les modifications sont annulables d'un simple Ctrl+Z.

> 🔵 **Expert** — MIrAI est une extension OXT qui communique avec un backend compatible OpenAI (OpenWebUI, Ollama, Scaleway…). La configuration (URLs, tokens, modèle) est gérée par un mécanisme de **Device Management** : au premier lancement, l'extension contacte un serveur bootstrap qui lui fournit sa configuration complète. Le traitement est en streaming (SSE) avec gestion des tokens, retry automatique et filtrage des balises de raisonnement (`<think>`). Aucune donnée ne transite par des serveurs tiers non configurés.

---

## 2. Installation

### 2.1 Prérequis

- **LibreOffice** 7.x ou supérieur
- **Accès réseau** au serveur backend IA (configuré par votre administrateur ou en local)
- Python 3.9+ (déjà inclus avec LibreOffice, aucune action nécessaire)

### 2.2 Installer l'extension

> 🟢 **Novice** — Installation pas-à-pas via l'interface graphique :
>
> 1. Téléchargez le fichier `mirai.oxt` (fourni par votre administrateur ou depuis le dépôt)
> 2. Ouvrez LibreOffice (Writer ou Calc)
> 3. Allez dans **Outils → Gestionnaire d'extensions**
> 4. Cliquez sur **Ajouter…**
> 5. Sélectionnez le fichier `mirai.oxt`
> 6. Acceptez la licence et redémarrez LibreOffice
> 7. Le menu **🤖 MIrAI 🤖** apparaît dans la barre de menus

> 🔵 **Expert** — Installation en ligne de commande :
>
> ```bash
> # Build + install + redémarrage en une commande
> ./scripts/dev-launch.sh
>
> # Ou étape par étape :
> ./scripts/02-build-oxt.sh              # génère dist/mirai.oxt
> ./scripts/02-build-oxt.sh --install    # build + install via unopkg
>
> # Avec un profil de configuration spécifique :
> ./scripts/02-build-oxt.sh --config config/profiles/docker.json
>
> # Reset complet (désinstall + purge cache + réinstall) :
> ./scripts/00-clean-install.sh --uninstall
> ./scripts/dev-launch.sh
> ```

### 2.3 Premier lancement

Au premier lancement après l'installation, MIrAI effectue automatiquement :

1. **Enrôlement** — Un assistant de configuration s'affiche si le Device Management est activé. Il vérifie la connexion au serveur et récupère la configuration (URL du backend, modèle, token API).
2. **Vérification de la connexion** — MIrAI teste l'accès au backend IA. Un indicateur de statut confirme que tout est opérationnel.
3. **Prêt à l'emploi** — Le menu **🤖 MIrAI 🤖** est disponible. Vous pouvez commencer à utiliser les fonctionnalités.

Si l'assistant d'enrôlement ne s'affiche pas, la configuration a été fournie automatiquement par votre administrateur.

---

## 3. Fonctionnalités Writer

Toutes les fonctionnalités Writer sont accessibles depuis le menu **🤖 MIrAI 🤖** ou par raccourci clavier. Elles agissent sur le **texte sélectionné** dans votre document.

### 3.1 ✨ Générer la suite 

Génère la continuation naturelle du texte sélectionné. L'IA écrit la suite comme si elle en était l'auteur.

> 🟢 **Novice** — Mode d'emploi :
>
> 1. Sélectionnez un passage de texte (un début de phrase, un paragraphe…)
> 2. Ouvrez l'assistant (**Ctrl+Alt+Espace**, macOS **Ctrl+Opt+Espace**) puis cliquez sur **Générer la suite**
> 3. Le texte généré apparaît directement après votre sélection, entre des marqueurs `---début-du-texte-généré---` et `---fin-du-texte-généré---`
> 4. Relisez le résultat et supprimez les marqueurs si le texte vous convient
>
> *Exemple : sélectionnez "Le projet vise à améliorer" et l'IA complétera avec la suite logique de votre texte.*

> 🔵 **Expert** — Le prompt système injecte une directive de continuation directe (pas de question, pas de reformulation). Si le modèle répond par une question malgré tout, un **retry automatique** est déclenché avec un prompt renforcé. Le budget de tokens est configurable via `extend_selection_max_tokens` (défaut : 15 000). Le prompt système additionnel peut être personnalisé via `extend_selection_system_prompt`.

### 3.2 🖊️ Modifier la sélection 

Ouvre une boîte de dialogue pour donner une instruction libre à l'IA sur le texte sélectionné.

> 🟢 **Novice** — Mode d'emploi :
>
> 1. Sélectionnez le texte à modifier
> 2. Ouvrez l'assistant (**Ctrl+Alt+Espace**, macOS **Ctrl+Opt+Espace**) puis cliquez sur **Modifier la sélection**
> 3. Une fenêtre s'ouvre avec un champ de saisie
> 4. Tapez votre instruction (ex. : "Traduis en anglais", "Rends plus formel", "Corrige les fautes")
> 5. Cliquez sur **Envoyer**
> 6. Le résultat apparaît après votre sélection, entre des marqueurs
>
> *Exemple : sélectionnez un email informel, tapez "Reformule en style professionnel" et validez.*

#### Boîte de dialogue d'édition

La fenêtre de modification contient :
- Un **champ de saisie** pour votre instruction
- Un bouton **Envoyer** pour lancer le traitement
- Un lien **Ouvrir prompt.txt** pour accéder au fichier de prompt personnalisable

#### Suggestions contextuelles IA

Sous le champ de saisie, une liste de **suggestions** s'affiche automatiquement. Ces suggestions sont générées par l'IA en fonction du texte sélectionné (ou du contenu du document si rien n'est sélectionné).

- Pendant le chargement, le label affiche "Mirai prépare des suggestions . . ."
- Cliquez sur une suggestion pour la copier dans le champ de saisie
- Le bouton **Nouvelles suggestions** régénère la liste

Si la génération IA échoue, des suggestions génériques sont proposées (correction, reformulation, simplification…).

> 🔵 **Expert** — Les suggestions sont générées via un appel LLM séparé (max 600 tokens). Le prompt impose une liste numérotée de 8 instructions en français. Les blocs `<think>` et les lignes non numérotées sont filtrés pour éviter que le raisonnement du modèle n'apparaisse dans les suggestions. Le prompt système de la modification est configurable via `edit_selection_system_prompt`.

### 3.3 📏 Ajuster la longueur 

Ouvre un mini-dialogue flottant avec deux boutons **−** et **+** pour réduire ou développer le texte sélectionné.

> 🟢 **Novice** — Mode d'emploi :
>
> 1. Sélectionnez le paragraphe à ajuster
> 2. Ouvrez l'assistant (**Ctrl+Alt+Espace**, macOS **Ctrl+Opt+Espace**) puis cliquez sur **Ajuster la longueur**
> 3. Un petit dialogue apparaît avec deux boutons : **−** (raccourcir) et **+** (allonger)
> 4. Cliquez sur le bouton souhaité
> 5. Le texte sélectionné est **remplacé** par la version ajustée
> 6. Le label de statut indique le résultat : "OK (42 mots, -18). Ctrl+Z pour annuler."
> 7. Si le résultat ne vous convient pas, faites **Ctrl+Z** pour revenir en arrière
>
> *Exemple : votre résumé fait 200 mots mais il en faut 130 ? Sélectionnez-le, Ctrl+Alt+Espace, cliquez −, et l'IA le raccourcit.*

#### Mini-dialogue − / +

Le dialogue reste ouvert et affiche :
- Un **label de statut** indiquant l'état (en attente, en cours, terminé avec le décompte de mots)
- Une **zone de prévisualisation** qui montre le flux de réponse de l'IA en temps réel (avec auto-scroll)
- Les deux boutons **−** et **+** toujours accessibles

#### Fonctionnement itératif

Vous pouvez cliquer plusieurs fois de suite pour affiner progressivement :
- **−** réduit d'environ 35% à chaque passe
- **+** développe d'environ 40% à chaque passe

Après chaque clic, re-sélectionnez le nouveau texte et cliquez à nouveau. Chaque opération est annulable individuellement par Ctrl+Z.

> 🔵 **Expert** — Le prompt inclut le nombre de mots de l'original et un objectif précis (ex. : "57 mots maximum" pour une réduction). Le résultat est appliqué via `rng.setString()` (remplacement en place, pas d'insertion après). Les blocs `<think>` sont filtrés avec trois passes regex (blocs complets, `</think>` en début, `<think>` non fermé en fin). La zone de prévisualisation affiche le flux brut y compris le raisonnement pour transparence, mais seul le texte nettoyé est écrit dans le document.

### 3.4 📝 Résumer la sélection 

Génère un résumé concis du texte sélectionné.

> 🟢 **Novice** — Mode d'emploi :
>
> 1. Sélectionnez un long passage (rapport, article, notes…)
> 2. Ouvrez l'assistant (**Ctrl+Alt+Espace**, macOS **Ctrl+Opt+Espace**) puis cliquez sur **Résumer la sélection**
> 3. Le résumé apparaît après votre sélection, entre `---début-du-résumé---` et `---fin-du-résumé---`
> 4. Votre texte original est conservé intact
>
> *Exemple : sélectionnez un compte-rendu de réunion de 3 pages, Ctrl+Alt+Espace, et obtenez les points clés en quelques lignes.*

> 🔵 **Expert** — Le prompt impose un résumé "ultra-concis" dans la même langue que l'original. Le budget de tokens est configurable via `summarize_selection_max_tokens`. Des stop phrases (`[END]`, `---END---`) sont détectées en streaming pour couper le flux si le modèle tente d'ajouter du contenu superflu.

### 3.5 💬 Reformuler la sélection 

Réécrit le texte sélectionné en langage clair et accessible, en conservant le sens.

> 🟢 **Novice** — Mode d'emploi :
>
> 1. Sélectionnez un passage complexe ou technique
> 2. Ouvrez l'assistant (**Ctrl+Alt+Espace**, macOS **Ctrl+Opt+Espace**) puis cliquez sur **Reformuler la sélection**
> 3. La version simplifiée apparaît après votre sélection, entre `---reformulation-du-texte---` et `---fin-de-reformulation---`
> 4. Votre texte original est conservé intact
>
> *Exemple : sélectionnez un article juridique, Ctrl+Alt+Espace, et obtenez une version compréhensible par tous.*

> 🔵 **Expert** — Le prompt système demande des phrases courtes, des mots courants, la voix active. La détection de questions conversationnelles est active (patterns : "voulez-vous", "souhaitez-vous", "would you like"…). Si le modèle pose une question, un message d'avertissement remplace le résultat. Le budget de tokens est configurable via `simplify_selection_max_tokens`.

---

## 4. Fonctionnalités Calc

Les fonctionnalités Calc sont accessibles depuis le menu **🤖 MIrAI 🤖** dans LibreOffice Calc.

### 4.1 🔄 Transformer → colonne résultat (Ctrl+Alt+Espace)

Applique une instruction IA sur une plage de cellules et écrit les résultats dans une colonne adjacente.

> 🟢 **Novice** — Mode d'emploi :
>
> 1. Sélectionnez une colonne de données (ex. : une liste de noms de produits)
> 2. Ouvrez l'assistant (**Ctrl+Alt+Espace**, macOS **Ctrl+Opt+Espace**) puis cliquez sur **Transformer → colonne résultat**
> 3. Tapez votre instruction (ex. : "Traduire en anglais", "Classifier par catégorie")
> 4. Les résultats apparaissent dans la **colonne libre suivante**, avec l'en-tête "Résultat IA"
> 5. Vos données originales ne sont pas modifiées
>
> *Exemple : vous avez 50 descriptions de produits en français ? Sélectionnez la colonne, Ctrl+Alt+Espace, tapez "Traduire en anglais", et la traduction apparaît à côté.*

> 🔵 **Expert** — L'extension détecte automatiquement la première colonne libre à droite de la sélection. L'en-tête de la colonne de résultat reprend le style de la ligne d'en-tête existante. Le traitement est séquentiel (cellule par cellule) avec le contexte de chaque ligne.

### 4.2 🧮 Générer une formule (Ctrl+Alt+Espace)

Crée une formule LibreOffice Calc à partir d'une description en langage naturel.

> 🟢 **Novice** — Mode d'emploi :
>
> 1. Sélectionnez la cellule où vous voulez la formule
> 2. Ouvrez l'assistant (**Ctrl+Alt+Espace**, macOS **Ctrl+Opt+Espace**) puis cliquez sur **Générer une formule**
> 3. Décrivez ce que vous voulez (ex. : "Somme des ventes si région = Nord")
> 4. La formule est insérée directement dans la cellule
>
> *Exemple : placez-vous en D2, Ctrl+Alt+Espace, tapez "Moyenne de la colonne B si la colonne A contient 'Paris'", et l'IA génère la formule AVERAGEIF correspondante.*

> 🔵 **Expert** — Le prompt injecte automatiquement le contexte de la feuille (en-têtes de colonnes, plage de données, valeurs de la ligne courante). Si une plage multi-lignes est sélectionnée, la formule est répliquée sur toutes les lignes avec ajustement des références. En cas d'erreur (`#VALEUR!`, `#REF!`…), la formule est renvoyée au modèle pour correction (conversation multi-tour).

### 4.3 📊 Analyser la plage (Ctrl+Alt+Espace)

Analyse une plage de cellules et produit un résumé des tendances, anomalies et points notables.

> 🟢 **Novice** — Mode d'emploi :
>
> 1. Sélectionnez une plage de données (ex. : un tableau de chiffres de ventes)
> 2. Ouvrez l'assistant (**Ctrl+Alt+Espace**, macOS **Ctrl+Opt+Espace**) puis cliquez sur **Analyser la plage**
> 3. Le résumé apparaît dans une cellule fusionnée sous votre sélection
>
> *Exemple : sélectionnez un tableau trimestriel, Ctrl+Alt+Espace, et obtenez "Hausse de 12% au T3, baisse notable en juillet, valeur aberrante en cellule C7".*

> 🔵 **Expert** — Le résultat est inséré deux lignes sous la sélection. La cellule de résultat est fusionnée sur la largeur de la sélection pour une meilleure lisibilité.

---

## 5. Paramètres et configuration

### 5.1 🟢 Via le menu MIrAI → Paramètres

Accessible depuis le menu **🤖 MIrAI 🤖 → ⚙️ Paramètres**. Vous pouvez y configurer :

- **URL du backend** — L'adresse du serveur IA
- **Modèle par défaut** — Le modèle de langage à utiliser
- **Token API** — Votre clé d'authentification
- **Proxy** — Si votre réseau nécessite un proxy

Dans la plupart des cas, ces paramètres sont préconfigurés par votre administrateur via le Device Management.

### 5.2 🔵 Fichiers de configuration et profils

| Fichier | Rôle |
|---------|------|
| `config/config.default.json` | Valeurs par défaut packagées dans l'OXT |
| `config/config.default.example.json` | Exemple committable (sans secrets) |
| `config/profiles/` | Profils prédéfinis (`docker`, `kubernetes`, `dgx`, `local-llm`) |

Au premier lancement, les réglages sont initialisés depuis `config.default.json` puis enrichis par le bootstrap Device Management. Les fichiers de l'utilisateur se trouvent dans le dossier `mirai/` :

```
~/Library/Application Support/LibreOffice/4/user/config/mirai/    # macOS
~/.config/libreoffice/4/user/config/mirai/                        # Linux
%APPDATA%\LibreOffice\4\user\config\mirai\                      # Windows
```

#### Vos données

L'extension ne garde sur votre poste que ses réglages, votre conversation et son journal, dans ce dossier. Aucun mot de passe ni jeton n'y est écrit (hors flux de télémétrie sécurisée hérité, inactif en pratique, voir donnees-locales.md) : ils sont confiés au coffre de votre système (Gestionnaire d'identification sous Windows, trousseau sous macOS). Sous Linux, ils ne sont gardés qu'en mémoire : l'assistant de connexion s'affiche à chaque session LibreOffice. Si vous désinstallez l'extension depuis le Gestionnaire des extensions de LibreOffice, tout est effacé ; redémarrez ensuite LibreOffice. Détails pour votre support : [donnees-locales.md](donnees-locales.md).

Pour construire un OXT avec un profil spécifique :

```bash
./scripts/02-build-oxt.sh --config config/profiles/docker.json
```

### 5.3 🔵 Proxy et certificats SSL

Le proxy se règle depuis **🤖 MIrAI 🤖 → ⚙️ Paramètres**, bouton **Proxy**. Les réglages sont enregistrés dans `mirai/settings.json` ; le mot de passe du proxy, lui, va dans le coffre du système (voir [Vos données](#vos-données)) :

```json
{
  "proxy_enabled": false,
  "proxy_url": "proxy.example.local:8080",
  "proxy_allow_insecure_ssl": true,
  "proxy_username": ""
}
```

Un bundle de certificats CA peut être spécifié via `ca_bundle_path`. Par défaut, le bundle Scaleway est inclus dans l'extension.

### 5.4 🔵 Personnaliser les prompts système

Chaque fonctionnalité dispose d'un prompt système configurable via `config.json` :

| Clé de configuration | Fonctionnalité |
|---------------------|----------------|
| `extend_selection_system_prompt` | Générer la suite |
| `edit_selection_system_prompt` | Modifier la sélection |
| `summarize_selection_system_prompt` | Résumer la sélection |
| `simplify_selection_system_prompt` | Reformuler la sélection |
| `systemPrompt` | Prompt système global (ajouté à toutes les requêtes) |

Les budgets de tokens sont également configurables :

| Clé | Défaut |
|-----|--------|
| `extend_selection_max_tokens` | 15 000 |
| `edit_selection_max_new_tokens` | 15 000 |
| `summarize_selection_max_tokens` | 15 000 |
| `simplify_selection_max_tokens` | 15 000 |

Un fichier `prompt.txt` placé à côté du document est détecté automatiquement et peut être ouvert depuis la boîte de dialogue d'édition.

---

## 6. Bonnes pratiques

> 🟢 **Novice** — Conseils essentiels :
>
> - **Sélectionnez avant d'agir** — Toutes les fonctionnalités agissent sur le texte sélectionné. Pas de sélection = pas de résultat (ou traitement du document entier pour certaines fonctions).
> - **Ctrl+Z est votre filet de sécurité** — Chaque action IA est annulable. N'hésitez pas à essayer puis annuler.
> - **Relisez toujours le résultat** — L'IA est un assistant, pas un rédacteur infaillible. Le résultat peut nécessiter des ajustements.
> - **Plus le texte sélectionné est long, plus le contexte est riche** — Pour de meilleurs résultats, sélectionnez un passage suffisamment long (au moins une phrase complète).
> - **Les marqueurs `---début---` / `---fin---`** — Ils délimitent le texte généré. Supprimez-les une fois le résultat validé.

> 🔵 **Expert** — Conseils avancés :
>
> - **Choix du modèle** — Les modèles plus grands (70B+) produisent des résultats de meilleure qualité mais sont plus lents. Les modèles de raisonnement (deepseek-r1) ajoutent une latence due à la phase de réflexion, mais leurs balises `<think>` sont filtrées automatiquement.
> - **Température** — Configurable via le backend. Une température basse (0.3-0.5) convient pour la correction et la reformulation, une température plus haute (0.7-1.0) pour la génération créative.
> - **Limites de tokens** — Si le résultat est tronqué, augmentez le budget de tokens dans la configuration. Attention : certains modèles ont des limites de contexte strictes.
> - **prompt.txt** — Placez un fichier `prompt.txt` à côté de votre document pour injecter des instructions persistantes dans le dialogue d'édition.

---

## 7. Résolution de problèmes

**"Rien ne se passe quand je clique"**
- Vérifiez que du texte est sélectionné
- Vérifiez la connexion au backend via MIrAI → Paramètres
- Redémarrez LibreOffice

**"Le texte généré est en anglais"**
- Le modèle peut basculer en anglais si le texte source est en anglais. Les prompts système imposent le français, mais certains modèles sont moins obéissants. Essayez de reformuler votre instruction en précisant "en français".

**"Le modèle pose une question au lieu de répondre"**
- Cela arrive avec certains modèles conversationnels. L'extension détecte ce cas et propose de réessayer. Si le problème persiste, sélectionnez davantage de contexte.

**"Erreur de connexion"**
- Vérifiez votre accès réseau
- Vérifiez l'URL du backend dans Paramètres
- Si vous êtes derrière un proxy, configurez-le dans les paramètres

**"Token invalide"**
- Une boîte de dialogue vous proposera d'ouvrir les paramètres pour vérifier votre token API

> 🔵 **Expert** — Diagnostic avancé :
>
> - **Logs** — Toutes les requêtes et erreurs sont tracées dans `mirai/mirai.log`, dans le dossier de configuration de LibreOffice (voir [donnees-locales.md](donnees-locales.md)). Consultez ce fichier pour diagnostiquer les problèmes de connexion, les erreurs HTTP, ou les réponses inattendues du modèle.
> - **Deepseek-r1 think blocks** — Si du texte de raisonnement apparaît dans le document (phrases commençant par "Okay, I'm looking at…"), c'est que le modèle produit sa réflexion sans balises `<think>`. L'extension filtre ces balises mais le texte de raisonnement non balisé peut passer. Utilisez un modèle qui respecte la convention `<think>` ou un modèle non-raisonnement.
> - **Limitations connues** — Certains modèles modifient les sauts de ligne ou la ponctuation. Les modèles très verbeux peuvent épuiser le budget de tokens en préambule. Performances optimales en français et en anglais.
> - **Reset complet** — `./scripts/00-clean-install.sh --uninstall` puis `./scripts/dev-launch.sh`

---

## 8. Raccourcis clavier (aide-mémoire)

### Writer

| Raccourci | Action |
|-----------|--------|
| Ctrl+Alt+Espace | ✨ Générer la suite |
| Ctrl+Alt+Espace | 🖊️ Modifier la sélection |
| Ctrl+Alt+Espace | 📏 Ajuster la longueur |
| Ctrl+Alt+Espace | 📝 Résumer la sélection |
| Ctrl+Alt+Espace | 💬 Reformuler la sélection |

### Calc

| Raccourci | Action |
|-----------|--------|
| Ctrl+Alt+Espace | 🔄 Transformer → colonne résultat |
| Ctrl+Alt+Espace | 🧮 Générer une formule |
| Ctrl+Alt+Espace | 📊 Analyser la plage |

### Communs

| Raccourci | Action |
|-----------|--------|
| Ctrl+Z | Annuler la dernière action IA |

---

*Notice générée pour MIrAI — Extension LibreOffice du programme MIrAI du ministère de l'Intérieur.*
