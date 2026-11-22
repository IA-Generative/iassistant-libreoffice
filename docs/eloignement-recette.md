# Éloignement dans Writer — essai de bout en bout

**Branche** : `exp/eloignement` · **Environnement** : bêta, dossiers fictifs
uniquement · Conception : [eloignement-conception.md](eloignement-conception.md)

Cet essai vérifie le parcours complet, de Writer jusqu'au dossier dans
Éloignement, à travers le relais du DM. Comptez une heure. Chaque
étape dit quoi faire et ce que vous devez voir.

## Prérequis

| Quoi | Valeur |
|---|---|
| Éloignement | bêta5 ou plus récente, routes /v1 de la conception §6 |
| Compte d'agent | Le compte MirAI de la personne connectée sur le poste : le DM tire l'adresse de l'agent de SON jeton. En bêta, ce compte est rattaché à la préfecture Seine-Saint-Denis, qui a les dix modèles de l'application (OQTF 611-1 1° à 4°, OQTF UE, OQTF UE réadmission, placement en CRA, information du procureur, procédure contradictoire, assignation à résidence) et le « Courrier d'essai IAssistant » (chemin « Essais IAssistant »). Le Courrier d'essai n'a ni bloc conditionnel ni option et porte six balises `etranger.*` ; les modèles de l'application ont des blocs relatorio, seule la génération par Éloignement s'y applique. `agent.essai-nuit@exemple.gouv.fr` ne sert qu'aux appels directs avec le jeton de service, pas à travers le DM. |
| DM | relais d'outils actif (`DM_TOOLS_ENABLED`), service `eloignement` déclaré pour le profil du poste, `ELOIGNEMENT_CLIENT_SECRET` posé ; `toolsEndpoint` présent dans `/config` |
| Interrupteur | `eloignement: true` dans les `featureToggles` du gabarit du plugin, pour la cohorte d'essai. Pour un poste isolé : `"eloignement_enabled": true` dans les réglages locaux. |
| Poste | OXT de la branche installé, poste enrôlé, session MirAI ouverte (Keycloak) avec ce compte |
| Dossiers fictifs | Dossier actif n° 13 : EXEMPLE-FICTIF Karim, né le 01/01/1990, nationalité MA, n° étranger 9999999901, en instruction, mesure envisagée « OQTF sans délai de départ volontaire » (il porte déjà deux actes d'essai). Pour l'étape 3.6, un dossier brouillon et un dossier archivé de la préfecture, à créer sous votre identité avec des identités fictives ; sans eux, sautez 3.6. |

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
   Éloignement » liste onze modèles, dont « Essais IAssistant › Courrier
   d'essai IAssistant ».
2. Tapez « essai », « Rechercher ». **Attendu** : la liste se restreint au
   Courrier d'essai ; avec « zzz », elle est vide.
3. Choisissez le modèle, « Valider ». **Attendu** : la fenêtre « Rechercher un
   dossier ».
4. Tapez « EXEMPLE-FICTIF Karim 01/01/1990 », Entrée. **Attendu** : une ligne
   « EXEMPLE-FICTIF Karim · né(e) le 01/01/1990 · … · dossier n° 13 ».
5. Sélectionnez la ligne. **Attendu** : l'en-tête du dossier (identité,
   nationalité, n° étranger, mesure envisagée).
6. Sélectionnez le dossier archivé, « Valider ». **Attendu** : refus, « Ce
   dossier est archivé : aucune pièce ne peut plus y être ajoutée. » Même
   essai avec le brouillon.
7. Revenez sur le dossier 13, « Valider ». **Attendu** : « Génération de l'acte
   dans Éloignement… », puis l'acte s'ouvre dans Writer tel quel, et le volet
   « Éloignement » à droite : « Lié au dossier n° … », la personne, le modèle,
   « Version : 1 », et la liste « À compléter » des balises restées vides.
8. Cliquez une balise de la liste. **Attendu** : elle est sélectionnée dans
   le document.
9. Fichier > Propriétés > Propriétés personnalisées. **Attendu** :
   `MirAI.Eloignement.Dossier`, `.Modele`, `.Document`, `.Version`. Rien dans
   le texte.

Si Éloignement refuse la génération (champs à confirmer, mesure que le
dossier ne permet pas), le message en donne la raison.

### 3 bis. Acte avec options de génération

1. « Éloignement », choisissez « OQTF 611-1 3° », « Valider », puis le
   dossier 13, « Valider ». **Attendu** : la fenêtre « Options de génération »,
   « Délai de départ volontaire » (liste : « Refusé », « 7 jours »… « 60
   jours ») et la durée d'interdiction de retour (liste : « Pas d'interdiction
   de retour », « 1 an »… « 5 ans »), rien de présélectionné.
2. « Valider » sans rien choisir. **Attendu** : « « … » est à renseigner. »
3. Choisissez « Refusé » et « 2 ans », « Valider ». **Attendu** :
   l'OQTF s'ouvre ; « À compléter » liste la délégation de signature et le
   tribunal administratif.
4. Essai de refus : « Placement en CRA » sur le dossier 13 (pas d'OQTF
   effective). **Attendu** : « Génération impossible : » suivi de la raison
   donnée par Éloignement. « Procédure contradictoire » sans fiche pénale
   répond encore « introuvable » : c'est une erreur de l'application, connue.

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
   remplir depuis un dossier », choisissez le dossier 13. **Attendu** : les
   balises sont remplies en texte ; le volet liste celles restées vides.
3. Ctrl+S. **Attendu** : premier versement (« Enregistré dans le dossier
   n° … »), l'acte apparaît dans Éloignement en version 1.
4. Essai de refus : même geste sur un modèle d'Éloignement qui contient des
   `${…}` ou des blocs conditionnels, par exemple `info_procureur.odt` du dépôt
   d'Éloignement ouvert depuis le poste. Le refus vient avant tout appel au
   service. **Attendu** : « Ce document contient
   des blocs conditionnels de modèle : seul Éloignement sait les évaluer. »

### 10. Actualiser depuis le dossier (parcours C)

1. Sur le document du 9, retouchez à la main une valeur remplie.
2. Dans Éloignement, modifiez une autre valeur du dossier.
3. Volet, « Actualiser depuis le dossier ». **Attendu** : la liste des écarts ;
   la valeur retouchée est listée « modifié à la main, non remplacé ».
4. « Appliquer ». **Attendu** : seule la valeur du dossier change, la retouche
   reste ; Ctrl+Z annule l'actualisation d'un coup.
5. Même essai sur un acte généré par Éloignement (étape 3, Courrier d'essai) :
   prénom changé dans le dossier, lieu de naissance retouché dans Writer.
   **Attendu** : le prénom est proposé ; le lieu de naissance est listé
   « modifié à la main, non remplacé » et reste tel quel.

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
