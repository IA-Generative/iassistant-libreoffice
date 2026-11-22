# Éloignement dans Writer — essai de bout en bout

**Branche** : `exp/eloignement` · **Environnement** : bêta, dossiers fictifs
uniquement · Conception : [eloignement-conception.md](eloignement-conception.md)

Cet essai vérifie le parcours complet, de Writer jusqu'au dossier dans
Éloignement, à travers le relais du DM. Comptez trois quarts d'heure. Chaque
étape dit quoi faire et ce que vous devez voir.

## Prérequis

| Quoi | Valeur |
|---|---|
| Éloignement | bêta (image 1.0.0-beta3 ou plus récente), routes /v1 de la conception §6 |
| Compte d'agent | Le compte MirAI de la personne connectée sur le poste : le DM tire l'adresse de l'agent de SON jeton. En bêta, ce compte est rattaché à la préfecture seine-saint-denis, qui n'a **aucun modèle d'acte**. Avant le parcours A, au choix : poser un modèle d'essai pour seine-saint-denis, ou rattacher temporairement le compte à la préfecture fictive « essai-nuit » (un modèle : « Information du procureur (essai) »). `agent.essai-nuit@exemple.gouv.fr` n'existe que dans le Keycloak d'Éloignement, sans mot de passe : il ne sert qu'aux appels directs avec le jeton de service, pas à travers le DM. |
| DM | relais d'outils actif (`DM_TOOLS_ENABLED`), service `eloignement` déclaré pour le profil du poste, `ELOIGNEMENT_CLIENT_SECRET` posé ; `toolsEndpoint` présent dans `/config` |
| Interrupteur | `eloignement: true` dans les `featureToggles` du gabarit du plugin, pour la cohorte d'essai. Pour un poste isolé : `"eloignement_enabled": true` dans les réglages locaux. |
| Poste | OXT de la branche installé, poste enrôlé, session MirAI ouverte (Keycloak) avec ce compte |
| Dossier fictif | un dossier actif de la préfecture d'essai (par exemple KOFFI Marc, né le 17/04/1992), un dossier brouillon et un dossier archivé |

Avant de commencer, vérifiez l'audience : le jeton Keycloak du poste doit être
accepté par le relais (`X-User-Authorization`). Un 401 à l'étape 2 avec
« session MirAI expirée ou refusée » alors que la session est ouverte désigne
ce point (audience vérifiée par le DM).

## Déroulé

### 1. Interrupteur éteint : aucun effet

1. Interrupteur éteint, ouvrez Writer et la palette (Ctrl+Alt+Espace).
2. **Attendu** : pas de chip « 🏛️ Éloignement », pas de barre « MIrAI — Éloignement ».
3. Enregistrez un document quelconque (Ctrl+S). **Attendu** : rien ne change.

### 2. Allumer, découvrir

1. Allumez l'interrupteur pour la cohorte, relancez LibreOffice.
2. Ouvrez un document Writer. **Attendu** : la barre « MIrAI — Éloignement »
   apparaît (bouton « Éloignement ») ; la palette montre la chip.

### 3. Nouvel acte (parcours A)

1. Cliquez « Éloignement ». **Attendu** : la fenêtre « Nouvel acte depuis
   Éloignement » montre « Essai nuit › Information du procureur (essai) ».
2. Tapez « procureur », « Rechercher ». **Attendu** : la liste se restreint.
3. Choisissez le modèle, « Valider ». **Attendu** : la fenêtre « Rechercher un
   dossier ».
4. Tapez « KOFFI Marc 17/04/1992 », Entrée. **Attendu** : une ligne
   « KOFFI Marc · né(e) le 17/04/1992 · … · dossier n° … ».
5. Sélectionnez la ligne. **Attendu** : l'en-tête du dossier (identité,
   nationalité, n° étranger, mesure envisagée).
6. Sélectionnez le dossier archivé, « Valider ». **Attendu** : refus, « Ce
   dossier est archivé : aucune pièce ne peut plus y être ajoutée. » Même
   essai avec le brouillon.
7. Revenez sur KOFFI Marc, « Valider ». **Attendu** : « Génération de l'acte
   dans Éloignement… », puis l'acte s'ouvre dans Writer tel quel, et le volet
   « Éloignement » à droite : « Lié au dossier n° … », la personne, le modèle,
   « Version : 1 », et la liste « À compléter » des balises restées vides.
8. Cliquez une balise de la liste. **Attendu** : elle est sélectionnée dans
   le document.
9. Fichier > Propriétés > Propriétés personnalisées. **Attendu** :
   `MirAI.Eloignement.Dossier`, `.Modele`, `.Document`, `.Version`. Rien dans
   le texte.

Si Éloignement refuse la génération (option de génération manquante, champs
à confirmer), le message le dit : réglez-les dans Éloignement et recommencez.

### 4. Enregistrer dans le dossier

1. Corrigez une phrase, Ctrl+S. **Attendu** : le volet passe par « En attente
   d'envoi… » puis « Enregistré dans le dossier n° … sous « … » » ; la version
   passe à 2.
2. Dans Éloignement, ouvrez le dossier. **Attendu** : l'acte, en version 2,
   avec la correction.

### 5. Éloignement ne répond pas

1. Coupez le réseau (ou le Wi-Fi), modifiez, Ctrl+S. **Attendu** : le fichier
   est enregistré sur le poste ; le volet dit « En attente d'envoi
   (service injoignable) » et propose « Réessayer l'envoi ».
2. Fermez LibreOffice, rétablissez le réseau, rouvrez l'acte (Fichier >
   Documents récents). **Attendu** : le volet revient, l'envoi repart seul
   (au plus tard 30 s après) : « Enregistré dans le dossier n° … ».

### 6. Conflit de version

1. Dans Éloignement, versez une nouvelle version de l'acte (ou faites-le
   depuis un second poste).
2. Dans Writer, modifiez, Ctrl+S. **Attendu** : la fenêtre « Conflit de
   version » ; rien n'est écrasé dans le dossier.
3. « Ouvrir la version du dossier ». **Attendu** : une seconde fenêtre avec la
   version du dossier, liée ; la vôtre reste ouverte, le volet dit « Conflit
   de version ».
4. Dans votre version, « Résoudre le conflit… », puis « Garder ma version
   comme nouvelle version ». **Attendu** : « Enregistré dans le dossier n° … »,
   nouvelle version dans Éloignement.

### 7. Copie sur le poste

1. Volet, « Enregistrer une copie sur le poste », choisissez le Bureau.
   **Attendu** : « Copie enregistrée sur le poste (elle garde le lien). » ;
   rien d'envoyé (la version ne bouge pas dans Éloignement).
2. Fermez, rouvrez la copie. **Attendu** : le volet « Lié au dossier n° … ».
3. Modifiez, Ctrl+S. **Attendu** : l'envoi part (même poste : pas de
   question), avec contrôle de version.

### 8. Lien reçu d'ailleurs

1. Copiez l'acte lié sur un autre poste (clé USB), ouvrez-le, modifiez, Ctrl+S.
   **Attendu** : la question « Ce document est lié au dossier n° …, mais ce
   lien n'a pas été créé sur ce poste. Enregistrer ce document dans ce
   dossier d'Éloignement ? ». « Non » : enregistré sur le poste seulement.

### 9. Remplir un document déjà ouvert (parcours B)

1. Ouvrez un document simple contenant des balises (champs « espace réservé »
   `<etranger.nom>`, `<etranger.prenom>`), sans bloc conditionnel.
2. « Éloignement », puis le lien « Ce document contient des balises : le
   remplir depuis un dossier », choisissez KOFFI Marc. **Attendu** : les
   balises sont remplies en texte ; le volet liste celles restées vides.
3. Ctrl+S. **Attendu** : premier versement (« Enregistré dans le dossier
   n° … »), l'acte apparaît dans Éloignement en version 1.
4. Essai de refus : même geste sur un modèle d'Éloignement qui contient des
   `${…}` ou des blocs conditionnels. **Attendu** : « Ce document contient
   des blocs conditionnels de modèle : seul Éloignement sait les évaluer. »

### 10. Actualiser depuis le dossier (parcours C)

1. Sur le document du 9, retouchez à la main une valeur remplie.
2. Dans Éloignement, modifiez une autre valeur du dossier.
3. Volet, « Actualiser depuis le dossier ». **Attendu** : la liste des écarts ;
   la valeur retouchée est marquée « (modifié à la main) » et décochée.
4. « Appliquer ». **Attendu** : seule la valeur cochée change ; Ctrl+Z annule
   l'actualisation d'un coup.

### 11. Données et traces

1. `<profil>/user/config/mirai/mirai.log` : pas de nom, de numéro de dossier
   ni de jeton (`x-user-authorization` masqué).
2. `mirai/eloignement/file_envoi.json` : vide après les envois ; aucune
   donnée du document dedans.
3. Éteignez l'interrupteur, relancez. **Attendu** : la barre
   « MIrAI — Éloignement » disparaît ; un document lié s'enregistre sur le
   poste comme d'habitude.

## Ce qu'il faut rapporter

Pour chaque étape non conforme : le numéro, ce que vous avez vu (le texte
exact du volet ou du message), l'heure. Joindre `mirai.log` ; il ne contient
ni contenu de document ni identité.
