# Éloignement dans Writer — conception

**Date** : 2026-11-22 · **Statut** : validé et implémenté (voir §10) ·
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
   Si le modèle exige des options de génération (délai de départ volontaire,
   durée d'interdiction de retour…), l'écran **E2b « Options de génération »**
   les demande, telles qu'Éloignement les décrit.
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
| **E2b — Options de génération** | dialogue modal | une ligne par option de `options_requises` du modèle : libellé d'Éloignement (à défaut, le code de l'option), liste déroulante si Éloignement donne les valeurs permises (Oui/Non pour un booléen), saisie libre sinon ; « Valider », « Annuler ». Absent pour un modèle sans option (courrier simple) |
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

- **Palette** : une chip « 🏛️ Éloignement » dans Writer, visible seulement
  quand la fonctionnalité est active. Elle ouvre E1, ou E3 si le document est
  déjà lié.
- **Barre d'outils** : une barre « MIrAI — Éloignement » avec un bouton
  « Éloignement », **créée à l'exécution** dans la configuration de Writer
  (`ModuleUIConfigurationManager`) quand l'interrupteur est allumé, et retirée
  quand il s'éteint. Un bouton déclaré dans `Addons.xcu` serait visible de
  tous les postes, interrupteur éteint compris.
- **Volet E3** : une fenêtre non modale posée sur la fenêtre du document (le
  patron éprouvé de la palette), une par document lié. *Écart assumé* : un
  vrai volet latéral demande une fabrique Python (`XUIElementFactory`) et un
  `Sidebar.xcu` qui afficherait l'onglet à tous les postes ; non vérifiable au
  banc cette nuit, il reste à faire si la fenêtre ne convient pas.

## 5. Architecture

### 5.1 Ce qui change dans la coquille

Trois retouches, sept lignes (borne `lines` du budget de la coquille relevée
d'autant, avec la raison) :

1. **`MainJob.trigger`** : l'action `Eloignement` appelle
   `core.entry.open_eloignement` (import paresseux).
2. **`MainJob.execute`** (événements `onLoad`, `onNew`) : appel de
   `core.entry.eloignement_on_load`. Interrupteur éteint : retour immédiat,
   sans import de l'interface (sauf pour retirer la barre d'outils créée du
   temps où il était allumé). DM pas encore joint au démarrage : l'écouteur
   d'enregistrement est posé sur un document déjà lié, pour qu'un Ctrl+S soit
   mis en file au lieu d'être perdu.
3. **`_redact_header_value`** : `x-user-authorization` est masqué dans le
   journal.

### 5.2 Moteur et interface

| Fichier | Rôle |
|---|---|
| `core/shell_facade.py` | + `tools_request` (relais d'outils : `toolsEndpoint`, paire relais, `X-User-Authorization`, transport de la coquille), `feature_state` / `feature_enabled` ; fonctions pures `build_tools_request`, `feature_from` |
| `core/entry.py` | + `open_eloignement`, `eloignement_on_load` |
| `core/eloignement/api.py` | client des routes /v1 (une table `ROUTES`), multipart en stdlib, erreurs → messages de la messagerie, identifiants validés avant d'entrer dans un chemin |
| `core/eloignement/link.py` | lien dans `UserDefinedProperties` |
| `core/eloignement/outbox.py` | file d'envoi persistante, rejeu, conflit |
| `core/eloignement/options.py` | options de génération : lecture de `options_requises`, corps `options` posé à chaque `champ` |
| `core/eloignement/fill.py` | balises (`TextField.JumpEdit` et texte `<a.b>`), insertion en texte, signets à empreinte, écarts pour « Actualiser », détection de la logique de modèle |
| `core/eloignement/session.py` | session du processus : documents liés, écouteur d'enregistrement, thread d'envoi, ouverture d'un acte |
| `core/eloignement/tools.py` | les tools, dans un registre à part (§5.5) |
| `ui/eloignement.py` | E1, E2, E2b, E3, E4, E5, barre d'outils, boîtes de message |
| `ui/palette.py` | + la chip |

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

| Tool | Rôle |
|---|---|
| `eloignement_lister_modeles` | R1 |
| `eloignement_rechercher_dossier` | R2 (recherche) |
| `eloignement_lire_dossier` | R2 (en-tête) |
| `eloignement_generer_acte` | R3 + R4, ouvre l'acte lié |
| `eloignement_remplir_depuis_dossier` | R5 + insertion dans le document ouvert |
| `eloignement_lire_ecarts` | R5 + comparaison aux signets |
| `eloignement_appliquer_ecarts` | écrit les écarts acceptés (une action d'annulation) |
| `eloignement_envoyer` | relance la file d'envoi (R6, R3-bis) |

Ces tools sont appelés directement par les écrans. Par défaut, ils ne sont **pas
proposés au modèle de langage** : les dossiers contiennent des données
personnelles, et rien ne doit partir vers un modèle sans geste de l'agent. Les
exposer au mode agentique est une question ouverte (§9).

### 5.6 Interrupteur de fonctionnalité

La fonctionnalité est active si deux conditions sont remplies, comme dans la
messagerie :

- l'interrupteur `eloignement` vaut strictement `true` dans les `features` du DM
  (`featureToggles` du gabarit du plugin, réglable par cohorte) ; à défaut de
  clé dans `features`, la clé de configuration `eloignement_enabled` (essais) ;
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

Figées côté Éloignement le 2026-11-22 (branche `exp/api-v1-dossiers`, déployées
en bêta3), appelées par `{toolsEndpoint}/eloignement/v1/…`.

| # | Besoin | Route | Utilisée par |
|---|---|---|---|
| R0 | découverte | `GET {toolsEndpoint}` → `services` contient `eloignement` | `Session.offered` |
| R1 | catalogue des modèles de la préfecture | `GET /v1/modeles?q=` → `{items:[{id, nom, chemin, version, natures, options_requises:[{option, champ}]}]}` | E1, E2b |
| R2 | recherche, en-tête | `GET /v1/dossiers?q=` (q interprété : mots, date, n° étranger, n° de dossier) ; `GET /v1/dossiers/{id}` (`statut_detaille`, `mesure_envisagee`, `_links.interface`) | E2, volet |
| R3 | générer | `POST /v1/dossiers/{id}/actes` `{modele_id, options?}` + `Idempotency-Key` → `{acte_id, nom, version, modele, balises_vides:[{nom, libelle, valeur}]}` ; `options` porte chaque option de `options_requises` à son `champ` (`oqtf.delai_depart_volontaire_jours` → `{"oqtf": {"delai_depart_volontaire_jours": 0}}`) ; 422 `OPTION_GENERATION_MANQUANTE`, 422 avec la raison quand le dossier ne permet pas la mesure, 409 `CHAMPS_A_CONFIRMER` | parcours A |
| R3-bis | premier versement d'un document rempli dans Writer | `POST /v1/dossiers/{id}/actes/fichier` (multipart `fichier`, `modele_id` facultatif) + `Idempotency-Key` → version 1 | parcours B, premier Ctrl+S |
| R4 | télécharger | `GET /v1/dossiers/{id}/actes/{acte_id}/contenu` → ODT + `ETag` | parcours A, « Ouvrir la version du dossier » |
| R5 | valeurs des balises | `GET /v1/dossiers/{id}/balises[?modele_id=]` → `{items:[{nom, libelle, valeur}]}` (filtre par modèle, le tri par balise se fait dans le plugin) | parcours B et C |
| R6 | nouvelle version | `POST /v1/dossiers/{id}/actes/{acte_id}/versions` (multipart `fichier`) + `If-Match` obligatoire (428) → 201 ; 412 avec `version`, `etag_courant`, `modifie_par`, `modifie_le` ; 409 archivé ; 415 hors ODT | Ctrl+S |
| R7 | actes d'un dossier | `GET /v1/dossiers/{id}/actes` → `{items:[{acte_id, nom, version, modele, modifie_le, modifie_par}]}` | volet (nom du modèle), « Garder ma version » sans 412 détaillée |

Écarts restants côté Éloignement, signalés à Éric :

- **Nom de l'acte** : Éloignement attribue le nommage existant de l'application
  (`2026_11_22_<NOM>_<modèle>.odt`, avec le nom de la personne), pas
  `dossier_type_rang`. Le plugin affiche et envoie le nom reçu, sans le recalculer.
- **Libellés des balises** : provisoires, tirés du nom (l'application n'en a pas).
- **Signets par balise à la génération** : non faits (moteur de rendu). Sans eux,
  « Actualiser depuis le dossier » ne marche que sur les passages remplis par
  le plugin (parcours B).
- **ODT fourni comme modèle** : versé tel quel (R3-bis), jamais rendu par
  Relatorio. Le plugin refuse donc « Remplir depuis un dossier » sur un document
  qui contient des blocs `if`/`for` ou des expressions `${…}`.

Contraintes du relais DM, relevées dans le code : préfixe `/v1/`, 60 Mo,
120 s, 60 appels par minute et par personne ; `If-Match`, `ETag`,
`Idempotency-Key` transmis ; `Content-Disposition` et `X-Nom-Fichier` **non**
transmis (le nom vient du corps JSON).

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
4. R3 doit-elle déduire les options de génération du dossier ? (Réponse
   d'Éloignement : non, le modèle les annonce dans `options_requises` et
   Writer les demande, §10.)
5. Éloignement doit-il poser des signets de balise à la génération (§6) ?
6. Un enregistrement sous un autre nom (« Enregistrer sous ») d'un document lié
   doit-il aussi envoyer une version ? (Proposé : oui. Seule « Enregistrer une
   copie » n'envoie rien.)

## 10. État de l'implémentation (2026-11-22)

Réponses retenues aux questions du §9, en attendant validation : retouches de
la coquille acceptées (7 lignes) ; tools **non** proposés au modèle de langage ;
actes ouverts dans le dossier de travail de MirAI ; « Enregistrer sous » envoie
aussi une version.

Ajouts nés de la mise en œuvre :

- **Lien reçu d'ailleurs** : un document lié dont le lien n'a pas été créé sur
  ce poste (pièce jointe, clé USB) ne verse rien sans confirmation de l'agent.
  Sinon un fichier piégé verserait en silence dans le dossier qu'il désigne.
- **Pas d'enregistrement à la place de l'agent** : après un envoi réussi, la
  nouvelle version est écrite dans les propriétés ; le fichier local n'est
  réenregistré que si l'agent n'a rien retouché entre-temps.
- **Désinstallation** : le dossier `mirai/` est effacé, y compris un envoi en
  attente ; le fichier enregistré par l'agent, lui, reste où il est.

- **Options de génération** (bêta4) : `options_requises` ne donne encore que
  le code et le `champ` de chaque option. E2b montre alors le code et un champ
  libre (une saisie numérique part en nombre). Demandé à Éloignement, en
  ajout : `libelle`, `type` (`texte`, `entier`, `booleen`, `choix`),
  `valeurs` [{valeur, libelle}] et `defaut`. E2b s'en sert dès qu'ils
  arrivent, sans changement du plugin.

Vérifié :

- 110 tests unitaires Éloignement (client, lien, file d'envoi, conflit, session,
  balises, options de génération, câblage), suite complète verte.
- E2b construit dans LibreOffice headless : liste déroulante des valeurs,
  refus d'une option manquante, corps `options` attendu.
- Dans LibreOffice 25.8 headless, sur les modèles réels d'Éloignement : repérage
  des 15 à 115 balises dans l'ordre du document, remplissage en texte brut avec
  signets, actualisation qui laisse un passage retouché à la main, annulation
  d'un seul Ctrl+Z, lien conservé par une copie, événements `OnSaveDone` et
  `OnCopyToDone`, construction et rendu du volet.

Pas encore vérifié : l'affichage réel des fenêtres (écran verrouillé cette
nuit) et le parcours de bout en bout à travers le DM, qui demande un poste
enrôlé et une connexion Keycloak. Le déroulé est dans
`docs/eloignement-recette.md`.
