# Éloignement dans Writer — conception

**Date** : 2026-11-22 · **Statut** : proposition, à valider avant tout code ·
**Branche** : `exp/eloignement` (locale, rien de poussé)

## 1. Objectif

Depuis Writer, l'agent de préfecture :

1. choisit un modèle d'acte ;
2. choisit un dossier ;
3. obtient l'acte rempli par Éloignement ;
4. le corrige ;
5. l'enregistre dans le dossier.

Il ne ressaisit rien. Éloignement reste la seule source pour les modèles, les
balises et leurs valeurs. Le plugin transporte, affiche et enregistre.

Le transport et l'identité sont ceux du plugin de messagerie (IAssistant). Le
plugin appelle `{toolsEndpoint}/eloignement/v1/…` avec la paire relais
(`X-Relay-Client`, `X-Relay-Key`) et `X-User-Authorization: Bearer <jeton
Keycloak>`. Le DM ajoute le jeton de service et `X-Agent-Email`. Le plugin ne
contient ni secret ni nom d'hôte d'Éloignement.

## 2. Parcours

### A. Nouvel acte (modèle, puis dossier)

1. L'agent ouvre **« Nouvel acte depuis Éloignement »**, depuis la palette ou le
   bouton de barre d'outils « Éloignement ».
2. L'écran **E1** montre le catalogue de sa préfecture, avec une recherche.
   L'agent choisit un modèle, puis clique sur « Valider ».
3. L'écran **E2** s'ouvre : **« Rechercher un dossier »**. L'agent choisit un
   dossier et contrôle son en-tête, puis clique sur « Valider ».
4. Éloignement génère l'acte. Le plugin télécharge l'ODT dans le dossier de
   travail (§5.3) et l'ouvre tel quel dans Writer, sans conversion.
5. Le plugin écrit le lien dans les propriétés personnalisées du document. Le
   volet **E3** s'ouvre et dit « Lié au dossier n° … ». Il liste les balises
   restées vides sous « À compléter ».

### B. Document déjà ouvert (dossier, puis valeurs)

1. Un document ouvert contient des balises, ou vient d'un modèle Éloignement.
2. L'agent choisit **« Remplir depuis un dossier »**, puis passe par E2.
3. Chaque balise reçoit sa valeur, insérée comme texte. Un signet nommé d'après
   la balise garde sa place, pour pouvoir actualiser plus tard. Les balises
   sans valeur restent visibles et sont listées dans E3.
4. Le document est lié (dossier, sans modèle Éloignement).

> **Limite** : un modèle Relatorio qui contient des blocs `if` ou `for` ne
> peut être évalué que par Éloignement. Le plugin ne recopie pas le moteur de
> rendu. Dans ce cas, il propose le parcours A ou la route R3-bis (§6).

### C. Actualiser depuis le dossier

1. Dans E3, l'agent choisit « Actualiser depuis le dossier ». Le plugin relit
   les valeurs (R5) et les compare au contenu de chaque signet.
2. L'écran **E5** liste les écarts sous la forme « balise : dans le document →
   dans le dossier ».
3. Un passage modifié à la main (contenu du signet ≠ dernière valeur insérée)
   est décoché par défaut. Rien n'est écrasé sans accord.
4. « Appliquer » écrit les valeurs cochées, dans une seule action d'annulation.

### D. Enregistrer

- **Ctrl+S sur un document lié** :
  1. LibreOffice enregistre normalement le fichier local. Le plugin n'empêche
     jamais l'enregistrement.
  2. Sur l'événement `OnSaveDone` (ou `OnSaveAsDone`) **de ce document
     seulement**, le plugin copie le fichier enregistré dans la file d'envoi.
  3. Il envoie une nouvelle version (R6) avec `If-Match: <version>`.
  4. Si l'envoi réussit, E3 affiche « Enregistré dans le dossier n° … ». Il
     montre la nouvelle version et le nom normalisé attribué par Éloignement
     (`dossier_type_rang`).
- **Éloignement ne répond pas** (0, 502, 503, 504, 429) : le fichier local est
  déjà enregistré. L'envoi reste dans la file et sera rejoué. E3 dit « En
  attente d'envoi ». Rien n'est perdu sans que l'agent le voie.
- **Conflit** (412, le document a changé dans Éloignement entre-temps) : le
  plugin n'écrase rien. L'écran **E4** propose « Garder ma version comme
  nouvelle version » ou « Ouvrir la version du dossier ». La seconde ouvre la
  version du dossier dans une autre fenêtre, et celle de l'agent reste ouverte.
- **« Enregistrer une copie sur le poste »** (commande de E3) : le document est
  enregistré par `storeToURL`, avec le sélecteur de fichier. Cela passe par
  l'événement `OnCopyToDone`, donc rien n'est envoyé. La copie garde ses
  propriétés : une fois rouverte, elle retrouve son dossier.
- **Document non lié** : aucun écouteur n'est posé et rien ne change.

## 3. Écrans

Ce sont des contrôles UNO et des jetons DSFR (`ui/dsfr.py`), comme la palette.
Les libellés sont ceux de la conception de la messagerie (onglet « Fiche »).

| Écran | Forme | Contenu |
|---|---|---|
| **E1 — Nouvel acte depuis Éloignement** | dialogue modal | champ de recherche, liste « Dossier › Modèle » du catalogue de la préfecture ; « Valider », « Annuler » |
| **E2 — Rechercher un dossier** | dialogue modal | un seul champ (« prénom nom date de naissance », n° étranger ou n° de dossier) et « Rechercher » ; chaque résultat tient sur une ligne : « NOM Prénom · né(e) le JJ/MM/AAAA · mesure · statut · dossier n° X » ; en dessous, l'en-tête du dossier sélectionné (identité, nationalité, n° étranger, mesure, statut) ; « Valider », « Annuler ». Brouillon ou archivé : ligne grisée, « Valider » inactif, infobulle reprise de la messagerie (« Ce dossier est archivé : aucune pièce ne peut plus y être ajoutée. ») |
| **E3 — volet « Éloignement »** | volet latéral de Writer | Document lié : « Lié au dossier n° X », personne, modèle, version, état de l'enregistrement (« Enregistré dans le dossier n° X » / « En attente d'envoi » / « Conflit de version » / « Modifications non enregistrées ») ; « À compléter » : libellé lisible et balise, un clic va à la balise ; boutons « Actualiser depuis le dossier », « Enregistrer une copie sur le poste », « Ouvrir le dossier dans Éloignement » (si le lien d'interface est fourni). Document non lié qui contient des balises : « Remplir depuis un dossier » |
| **E4 — conflit de version** | dialogue modal | « Le document a été modifié dans Éloignement depuis votre version n. » ; « Garder ma version comme nouvelle version », « Ouvrir la version du dossier », « Annuler » (l'envoi reste en attente) |
| **E5 — actualiser** | dialogue modal | tableau balise / dans le document / dans le dossier, une case par ligne (décochée si modifiée à la main) ; « Appliquer », « Annuler » |

Les erreurs reprennent les messages de la messagerie (`ELO_STATUS_MESSAGES`) :

| Code | Message |
|---|---|
| 0 | « service injoignable » |
| 401 | « session MirAI expirée ou refusée » |
| 403 | « accès refusé par Éloignement » |
| 404 | « dossier ou élément introuvable » |
| 409 | « opération impossible dans l'état actuel du dossier » |
| 413 | « pièce trop volumineuse pour le relais » |
| 429 | « trop de demandes : réessayer dans une minute » |
| 5xx | « service indisponible » |

S'il est présent, le `detail` du `problem+json` d'Éloignement passe avant ces
messages.

## 4. Points d'entrée

- **Palette** : une commande « Éloignement » dans Writer, visible seulement
  quand la fonctionnalité est active. Elle ouvre E1, ou E3 si le document est
  déjà lié.
- **Barre d'outils** : un bouton « Éloignement » (`oxt/Addons.xcu`,
  `OfficeToolBar`, contexte Writer), qui fait la même chose.
- **Volet latéral** : `oxt/Sidebar.xcu` déclare un deck et un panneau, et une
  fabrique Python (`XUIElementFactory`) les construit. C'est un composant UNO de
  plus, déclaré dans le manifeste comme `calc_prompt_function.py`.
  *Risque* : le plugin n'a encore aucun volet latéral. Si la fabrique Python
  pose problème au banc, le repli est une fenêtre non modale ancrée, comme la
  palette.

## 5. Architecture

### 5.1 Ce qui change dans la coquille (à valider)

La coquille ne change pas, sauf sur trois points, chacun de quelques lignes,
sur le modèle de `OpenAssistant` :

1. **`MainJob.trigger`** : une action `Eloignement`, qui fait un import
   paresseux de `core.eloignement.entry`.
2. **`MainJob.execute`** (événements `onLoad` et `onNew`, déjà câblés dans
   `Jobs.xcu`) : si la fonctionnalité est active, un appel paresseux qui pose
   l'écouteur d'enregistrement sur un document lié. C'est ainsi qu'une copie
   rouverte retrouve son dossier.
3. **`_curl_headers_for_log`** : ajouter `x-user-authorization` aux en-têtes
   masqués.

### 5.2 Moteur et interface

| Fichier | Rôle |
|---|---|
| `core/shell_facade.py` | + `tools_request(method, path, body, headers, timeout)` : URL depuis `toolsEndpoint`, en-têtes `_relay_headers()` + jeton utilisateur + `_with_user_agent`, transport `_urlopen` + `get_ssl_context`. + `feature_enabled("eloignement")` |
| `core/eloignement_api.py` | client des routes R1…R7, multipart en stdlib, erreurs → messages français |
| `core/eloignement_link.py` | lecture et écriture du lien dans `DocumentProperties.UserDefinedProperties` |
| `core/eloignement_queue.py` | file d'envoi persistante, rejeu, conflit |
| `core/eloignement_fill.py` | repérage des balises (`TextField.JumpEdit`), insertion en texte, signets, comparaison pour « Actualiser » |
| `core/tools/eloignement_tools.py` | les tools du registre (§5.5) |
| `ui/eloignement_dialogs.py` | E1, E2, E4, E5 |
| `ui/eloignement_panel.py` | E3 et la fabrique du volet |

Le threading suit les règles existantes :

- le réseau tourne dans un thread de travail ;
- tout accès UNO passe par `MainThreadDispatcher` ;
- `processEventsToIdle` n'est jamais appelé ;
- `core/` et `ui/` n'importent jamais `entrypoint`.

### 5.3 Lien et fichiers locaux

Les propriétés personnalisées du document, toutes de type texte, sont exactement
celles-ci :

| Propriété | Valeur |
|---|---|
| `MirAI.Eloignement.Dossier` | identifiant du dossier |
| `MirAI.Eloignement.Modele` | identifiant du modèle (vide pour le parcours B) |
| `MirAI.Eloignement.Document` | identifiant de l'acte dans Éloignement |
| `MirAI.Eloignement.Version` | dernière version connue (valeur de l'ETag) |

- Rien n'est écrit dans le texte du document.
- Le nom de la personne et le libellé du modèle ne sont pas stockés dans le
  document : E3 les relit (R2, R7). Hors ligne, il n'affiche que les numéros.
- Après un envoi réussi, la nouvelle version est écrite dans la propriété, puis
  le document est réenregistré en local par `store()`. Un indicateur ignore
  l'événement `OnSaveDone` qui en résulte, ce qui évite une boucle.
- Le dossier de travail est `<UserConfig>/mirai/eloignement/` : les actes
  ouverts et la file `file_envoi.json`.
  - L'écriture est atomique, comme `local_config`.
  - Le dossier est effacé à la désinstallation.
  - Il est ajouté à `docs/donnees-locales.md`.

### 5.4 File d'envoi

- **Une entrée** contient : dossier, document, version de base, chemin d'une
  copie figée du fichier enregistré, état, nombre d'essais, prochain essai.
  L'agent peut continuer à modifier le document, la copie figée ne bouge pas.
- **Un seul envoi par document** : un nouvel enregistrement remplace l'entrée en
  attente du même document, et la dernière version gagne.
- **Le rejeu** a lieu :
  - à l'enregistrement suivant ;
  - à l'ouverture de E3 ;
  - par un thread de fond (réseau seulement, sans UNO), avec un délai de 30 s
    porté progressivement à 10 min, tant que la file n'est pas vide.

  `Retry-After` est respecté.
- **412** : l'entrée passe à « conflit ». Elle n'est plus rejouée jusqu'au
  choix fait dans E4. **409** (dossier archivé) : l'entrée passe à « refusé »,
  avec le message, et le fichier local reste.
- **Une entrée n'est retirée** qu'après un 2xx.

### 5.5 Tools du registre

| Tool | Mutant | Rôle |
|---|---|---|
| `eloignement_lister_modeles` | | R1 |
| `eloignement_rechercher_dossier` | | R2 |
| `eloignement_generer_acte` | ✔ (ouvre un document) | R3 + R4 |
| `eloignement_remplir_depuis_dossier` | ✔ | R5 + insertion |
| `eloignement_actualiser` | ✔ | R5 + comparaison |
| `eloignement_enregistrer` | | R6 avec la file |

Ces tools sont appelés directement par les écrans. Par défaut, ils ne sont **pas
proposés au modèle de langage** : les dossiers contiennent des données
personnelles, et rien ne doit partir vers un modèle sans geste de l'agent. Les
exposer au mode agentique est une question ouverte (§9).

### 5.6 Interrupteur de fonctionnalité

La fonctionnalité est active si deux conditions sont remplies, comme dans la
messagerie :

- l'interrupteur `eloignement` vaut strictement `true` dans les `features` du DM
  (`featureToggles` du gabarit du plugin, réglable par cohorte) ;
- la découverte `GET {toolsEndpoint}` annonce le service `eloignement`
  (résultat gardé 5 min).

L'interrupteur est éteint par défaut. Quand il est éteint, il n'y a ni commande,
ni volet, ni écouteur.

### 5.7 Valeurs reçues = données

- Chaque valeur est insérée par `insertString` sur un curseur, comme texte brut.
- Le plugin ne crée jamais de champ, de macro ni d'hyperlien à partir d'une
  valeur.
- L'ODT généré par Éloignement est ouvert tel quel. Les macros restent soumises
  à la politique de sécurité de LibreOffice, et le plugin ne les active pas.
- La télémétrie ne contient que des compteurs, des statuts et des durées. Elle
  ne porte jamais de numéro de dossier, de nom ni de contenu, et le journal non
  plus.

## 6. Routes

Les routes passent par le relais DM sous le préfixe `/v1/`. Elles sont
appelées par `{toolsEndpoint}/eloignement/v1/…`.

| # | Besoin | Route visée | État le 2026-11-22 |
|---|---|---|---|
| R0 | découverte | `GET {toolsEndpoint}` → `services` contient `eloignement` | **existe** (DM, `exp/eloignement-relais`) |
| R1 | catalogue des modèles de la préfecture, avec recherche | `GET /v1/modeles?q=` → `[{id, nom, chemin, version}]` | **manquante**. Prévue « si le temps le permet » dans la PR brouillon #1720. Seul l'équivalent legacy existe (`/document-tree`, JWT agent, inaccessible au jeton de service) |
| R2 | recherche d'un dossier, en-tête | `GET /v1/dossiers?q=` interprété ; `GET /v1/dossiers/{id}` | **en cours** (#1720) : `q` multi-critères (mots, date, n° étranger, n° de dossier), `mesure_envisagee`, `statut_detaille` (dont `archive`), `inclure_archives` |
| R3 | générer un acte (modèle + dossier) | `POST /v1/dossiers/{id}/actes` `{modele_id, …}` → `{id, nom (dossier_type_rang), version, balises_vides:[{balise, libelle}]}` | **manquante**. Génération + octets : « si le temps le permet » (#1720). Balises vides avec libellés : **pas prévue** |
| R3-bis | générer depuis un ODT fourni (parcours B avec `if`/`for`) | même route, avec le fichier en multipart | **à demander** (optionnelle) |
| R4 | télécharger l'acte | `GET /v1/dossiers/{id}/actes/{doc}/contenu` → octets ODT + `ETag` | **manquante** (#1720, même lot que R3) |
| R5 | valeurs des balises d'un dossier, avec libellés | `GET /v1/dossiers/{id}/valeurs?balises=a,b` → `{valeurs:{balise: texte\|null}, libelles:{balise: texte}}` | **manquante, pas prévue cette nuit**. Aujourd'hui, le catalogue n'a pas de libellé lisible (`libelle` = la balise) |
| R6 | verser une nouvelle version d'un acte | `PUT /v1/dossiers/{id}/actes/{doc}/contenu` + `If-Match` → `{version, nom}` ; 412 en cas de conflit, 409 si le dossier est archivé | **manquante, pas prévue cette nuit**. `POST /v1/dossiers/{id}/documents` existe mais verse une *pièce entrante* dans le lot de dépôt, sans version : ne convient pas |
| R7 | liste des actes d'un dossier | `GET /v1/dossiers/{id}/actes` → `[{id, nom, modele, version, modifie_le, modifie_par}]` | **manquante** (#1720, « si le temps le permet ») |

Contraintes du relais DM, relevées dans le code :

- préfixe autorisé : `/v1/` ;
- taille maximale : 60 Mo ;
- délai : 120 s ;
- débit : 60 appels par minute, par personne et par service ;
- `If-Match` et `ETag` sont transmis.
- `Content-Disposition` n'est pas transmis. Le nom attribué doit donc venir dans
  le corps JSON, ce qu'Éloignement s'engage à faire.

Points à confirmer :

- **Options de génération.** Selon la nature du modèle, la génération legacy
  exige des options : délégation de signature, n° de mesure, OQTF… Elle répond
  409 `CHAMPS_A_CONFIRMER` s'il reste des champs à confirmer. Faut-il que R3 les
  déduise du dossier ? Sinon le plugin affiche le message et « Ouvrir le dossier
  dans Éloignement ». Un formulaire d'options dans Writer serait une
  duplication.
- **Repères à la génération.** Pour actualiser un acte généré par Éloignement
  (parcours C), il faut savoir où chaque valeur a été posée. Demande : qu'Éloignement entoure chaque valeur d'un signet nommé d'après sa balise. Sans
  cela, « Actualiser » ne marche que pour le parcours B.
- **Audience Keycloak.** `keycloakClientId` du plugin LibreOffice est servi par
  le DM depuis son propre `KEYCLOAK_CLIENT_ID`, qui sert aussi d'audience
  vérifiée sur `X-User-Authorization`. C'est probablement compatible, mais à
  vérifier au banc sur le `aud` du jeton.

## 7. Livraison par étapes

1. **Avec ce qui existe ou arrive cette nuit** : socle (interrupteur, découverte,
   `tools_request`), E2 sur R2, lien dans les propriétés, volet E3 sans
   enregistrement, tests unitaires du lien.
2. **Quand R1, R3, R4 et R7 sont prêtes** : parcours A complet, E1.
3. **Quand R5 et R6 sont prêtes** : parcours B et C, enregistrement versionné,
   file d'envoi, conflit (E4).

La file d'envoi et le conflit sont testés en unitaire dès l'étape 1, contre un
faux client.

## 8. Tests

Tests unitaires, avec les faux documents `tests/stubs/fake_docs.py` et un faux
client HTTP :

- **Lien** : écrire et relire les 4 propriétés ; une copie
  (`storeToURL`) rouverte garde le lien ; un document sans propriété n'est pas
  lié ; aucune écriture dans le texte.
- **File d'envoi** :
  - persistance et reprise après redémarrage ;
  - remplacement par la dernière version ;
  - 503 avec `Retry-After` : nouvel essai planifié ;
  - 2xx : entrée retirée et version mise à jour ;
  - un fichier corrompu ne perd pas l'entrée.
- **Conflit** : 412 donne l'état « conflit » sans écrasement ; « garder ma
  version » renvoie avec la nouvelle base ; « ouvrir la version du dossier »
  n'altère pas le document local.
- **Écouteur** : un document non lié n'a ni écouteur ni effet ; `OnCopyToDone`
  n'envoie rien ; il n'y a pas de boucle après `store()`.
- **Valeurs comme données** : une valeur qui contient `=`, `<a>`,
  `vnd.sun.star.script:` ou une URL est insérée telle quelle, en texte.
- **Règles existantes** : pas d'import de la coquille, pas de pompage
  d'événements, interrupteur éteint = aucun effet.

Le **déroulé de bout en bout** sera écrit pas à pas dans `docs/RECETTE.md`.
Il se joue sur l'environnement de bêta, avec des dossiers fictifs, et suit
cette trame :

1. activer `eloignement` pour la cohorte de test ;
2. vérifier la découverte ;
3. parcours A sur un dossier fictif ;
4. Ctrl+S, puis vérifier la version dans Éloignement ;
5. couper le réseau, puis Ctrl+S donne « En attente d'envoi », et le
   rétablissement donne « Enregistré… » ;
6. modifier l'acte dans Éloignement, puis Ctrl+S donne E4 ;
7. enregistrer une copie sur le poste, la rouvrir, et retrouver le lien ;
8. vérifier qu'un document non lié s'enregistre comme d'habitude.

## 9. Questions ouvertes

1. Les trois retouches de la coquille du §5.1 sont-elles acceptées ?
2. Les tools doivent-ils être proposés au modèle de langage (mode agentique),
   ou rester appelés seulement depuis les écrans en v1 ?
3. Où vit le fichier de travail d'un acte : le dossier de travail de MirAI
   (proposé), ou le dossier Documents de l'agent ?
4. R3 doit-elle déduire les options de génération du dossier ?
5. Éloignement doit-il poser des signets de balise à la génération (§6) ?
6. Un enregistrement sous un autre nom (« Enregistrer sous ») d'un document lié
   doit-il aussi envoyer une version ? (Proposé : oui. Seule « Enregistrer une
   copie » n'envoie rien.)
