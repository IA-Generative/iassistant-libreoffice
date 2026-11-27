# Recette utilisateur : assistant MIrAI pour LibreOffice

**Version testée : 0.4.0** · Document du 2026-11-17

Ce document sert à **vérifier que l'assistant fonctionne** en l'utilisant comme le ferait
n'importe qui. Aucune compétence technique n'est nécessaire : chaque scénario dit quoi faire
et ce que vous devez voir, puis vous cochez.

Comptez environ **une heure et quart**. Faites les scénarios dans l'ordre, certains
préparent les suivants. La partie 9 (mise à jour) se fait à part, avec la personne qui
publie la version.

> **À retenir avant de commencer** : si un scénario ne se passe pas comme décrit, ce n'est
> pas grave, c'est justement ce qu'on cherche. Notez ce que vous avez vu et passez au
> suivant. La partie 10 explique quoi rapporter.

---

## Avant de commencer

### Ce qu'il vous faut

- LibreOffice installé (version 25.8 ou plus récente).
- L'extension `mirai.oxt` fournie.
- Votre identifiant MIrAI habituel.
- Un document Writer de deux ou trois pages, avec des titres, quelques fautes d'orthographe
  et au moins une note de bas de page.

### Installer l'extension

1. **Fermez complètement LibreOffice** (tous les documents).
2. Double-cliquez sur le fichier `mirai.oxt`. LibreOffice s'ouvre et propose l'installation.
3. Acceptez. Un message confirme que l'extension est installée.
4. **Fermez LibreOffice, puis rouvrez-le.** L'extension ne devient active qu'au lancement
   suivant.

☐ Conforme ☐ Non conforme · *observations :* ______________________________

### Comment ouvrir l'assistant

Deux moyens équivalents, dans Writer comme dans Calc :

- le raccourci **Ctrl+Alt+Espace** (sur Mac : **Ctrl+Option+Espace**) ;
- le menu **🤖 MIrAI** en haut de la fenêtre, entrée **🤖 Ouvrir l'assistant (Ctrl+Alt+Espace)**.

> Le clic droit ne propose plus d'entrée MIrAI : c'est voulu. Toutes les actions passent
> par la fenêtre de l'assistant.

---

## 1 — Premier démarrage et connexion

> Sous Linux, LibreOffice ne garde pas votre connexion d'une session à l'autre : la fenêtre
> de connexion revient à chaque démarrage, c'est normal. Sous Windows et macOS, elle ne doit
> apparaître qu'une fois.

### 1.1 La fenêtre de connexion s'ouvre d'elle-même

**Faites :** après l'installation, ouvrez LibreOffice Writer avec un document vide. N'appuyez
sur rien.

**Vous devez voir :** au bout de quelques secondes, une fenêtre **« Bienvenue dans
IA'ssistant by MIrAI »** s'ouvre, avec *Étape 1/5 — Présentation* et deux boutons, **Plus
tard** et **Commencer**.

**Faites ensuite :** cliquez sur **Plus tard**. Fermez LibreOffice, puis rouvrez-le.

**Vous devez voir :** la fenêtre ne revient pas tant que LibreOffice reste ouvert, puis
réapparaît d'elle-même au lancement suivant.

☐ Conforme ☐ Non conforme · *observations :* ______________________________

### 1.2 La connexion aboutit

**Faites :** cliquez sur **Commencer**, puis sur **Ouvrir le navigateur**. Connectez-vous
dans le navigateur, puis revenez dans LibreOffice.

**Vous devez voir :**

- dans le navigateur, une page **« Authentification terminée »** avec **« Connexion
  validée »** ; vous pouvez fermer l'onglet ;
- dans LibreOffice, la fenêtre indique l'enregistrement de votre poste, puis affiche
  **« Enrôlement terminé ! »** en vert avec le bouton **🚀 Commencer à utiliser**.

> Si le navigateur affiche « Callback invalide », ou si rien ne se passe dans LibreOffice
> au bout de trois minutes : fermez tous les onglets de connexion MIrAI et recommencez avec
> **un seul** onglet.

☐ Conforme ☐ Non conforme · *observations :* ______________________________

### 1.3 La connexion est gardée (Windows et macOS)

**Faites :** fermez LibreOffice et rouvrez-le. Dans un document qui contient du texte,
ouvrez l'assistant et cliquez sur **📝 Résumer**, puis faites **Ctrl+Z**.

**Vous devez voir :** aucune demande de connexion, et un résumé qui s'écrit dans le
document. Sous Linux, notez seulement que la fenêtre de connexion est revenue,
reconnectez-vous et continuez.

☐ Conforme ☐ Non conforme · *observations :* ______________________________

---

## 2 — La fenêtre de l'assistant

### 2.1 Elle s'ouvre et montre ce sur quoi elle va travailler

**Faites :** dans un document Writer qui contient du texte, cliquez dans un paragraphe sans
rien sélectionner, puis **Ctrl+Alt+Espace**.

**Vous devez voir :** une fenêtre flottante titrée **« MIrAI — Assistant · »** suivi du nom
du document, avec de haut en bas :

- un bandeau bleu **« MIrAI — Assistant (Writer) »** ;
- six boutons : **📝 Résumer**, **💬 Simplifier**, **📏− Raccourcir**, **📏+ Allonger**,
  **✏️ Corriger**, **🌐 Traduire** ;
- une ligne qui indique **« Document entier »** ;
- un champ de saisie ;
- une ligne avec la case **Ajouter à la suite** et le bouton **Envoyer ⏎** ;
- une zone de texte, et sous elle les onglets **Conversation**, **Suggestions**,
  **Raisonnement** et **Actions**.

Aucun libellé n'est coupé.

☐ Conforme ☐ Non conforme · *observations :* ______________________________

### 2.2 L'indicateur suit votre sélection

**Faites :** sans fermer la fenêtre, sélectionnez une phrase dans le document. Cliquez
ensuite ailleurs dans le texte, sans sélectionner.

**Vous devez voir :** la ligne change à chaque fois : **« Sélection : « … » »** avec le
début de votre phrase, puis **« Document entier »**.

☐ Conforme ☐ Non conforme · *observations :* ______________________________

### 2.3 Une seule fenêtre

**Faites :** la fenêtre étant ouverte, choisissez à nouveau **🤖 MIrAI ▸ 🤖 Ouvrir
l'assistant**.

**Vous devez voir :** la même fenêtre passe au premier plan. Il n'y en a pas une seconde.

☐ Conforme ☐ Non conforme · *observations :* ______________________________

### 2.4 La fenêtre se ferme proprement

**Faites :** appuyez sur **Échap**. Rouvrez, cliquez dans la zone du bas, appuyez sur
**Échap**. Rouvrez, puis fermez avec la **croix**.

**Vous devez voir :** la fenêtre se ferme les trois fois. Le document est intact, aucun
message d'erreur.

☐ Conforme ☐ Non conforme · *observations :* ______________________________

---

## 3 — Les actions dans Writer

Pour chaque bouton : **sélectionnez un paragraphe entier**, ouvrez l'assistant, cliquez sur
le bouton. La case **Ajouter à la suite** reste **décochée** jusqu'au scénario 3.11 :
chaque action **remplace** alors le texte visé.

Pendant le travail, le texte du modèle s'affiche au fil de l'eau dans l'onglet
**Conversation**. Il s'écrit dans le document en une seule fois, à la fin.

### 3.1 Résumer

**Vous devez voir :** un résumé **remplace** votre paragraphe. La ligne de statut affiche,
en vert et en gras, **« ✓ Document modifié · Ctrl+Z pour annuler »**.

☐ Conforme ☐ Non conforme · *observations :* ______________________________

### 3.2 Une seule annulation suffit

**Faites :** juste après le scénario 3.1, appuyez **une seule fois** sur **Ctrl+Z**.

**Vous devez voir :** votre paragraphe d'origine revient en entier, d'un coup.

☐ Conforme ☐ Non conforme · *observations :* ______________________________

### 3.3 Simplifier · Raccourcir · Allonger

**Vous devez voir :** à la place du paragraphe, un résultat qui correspond à l'intitulé du
bouton. Raccourcir donne environ les deux tiers de la longueur, Allonger environ une fois
et demie.

| Bouton | Conforme | Non conforme | Observations |
|---|---|---|---|
| 💬 Simplifier | ☐ | ☐ | |
| 📏− Raccourcir | ☐ | ☐ | |
| 📏+ Allonger | ☐ | ☐ | |

### 3.4 Corriger

**Préparez :** un paragraphe comme `Les petits enfant mange des pomme rouge.`, où le mot
*petits* est en **gras**.

**Faites :** sélectionnez-le, cliquez sur **✏️ Corriger**.

**Vous devez voir :** seules les fautes sont corrigées, le reste ne bouge pas, et *petits*
**reste en gras**.

**Faites ensuite :** sélectionnez une phrase sans faute et cliquez sur **✏️ Corriger**.

**Vous devez voir :** « Aucune faute trouvée : rien n'a été modifié. », et le statut
**« Terminé »**.

☐ Conforme ☐ Non conforme · *observations :* ______________________________

### 3.5 Traduire

**Faites :** sélectionnez un paragraphe en français, tapez `anglais` dans le champ de
saisie, puis cliquez sur **🌐 Traduire**.

**Vous devez voir :** le paragraphe est remplacé par sa traduction en anglais.

**Faites ensuite :** sélectionnez un autre paragraphe en français et cliquez sur **🌐
Traduire** sans rien taper.

**Vous devez voir :** sans langue tapée, l'assistant traduit vers la langue de LibreOffice,
ici le français. Il n'écrit donc rien et l'explique : « Le texte est déjà dans la langue
demandée : rien n'a été écrit. Tapez la langue voulue dans le champ de saisie, puis cliquez
à nouveau sur Traduire. »

☐ Conforme ☐ Non conforme · *observations :* ______________________________

### 3.6 Les notes de bas de page restent

**Préparez :** un paragraphe qui contient une note de bas de page et une faute
d'orthographe.

**Faites :** sélectionnez-le et cliquez sur **✏️ Corriger**. Puis, sur le même paragraphe,
tapez `anglais` et cliquez sur **🌐 Traduire**.

**Vous devez voir :** avec Corriger, la faute est corrigée et la note reste à sa place, avec
son numéro. Avec Traduire, soit la traduction s'écrit et la note reste, soit rien n'est
écrit et un message explique que le passage contient une note que la réécriture
abîmerait. **La note ne disparaît jamais.**

☐ Conforme ☐ Non conforme · *observations :* ______________________________

### 3.7 Sans rien sélectionner : tout le document

**Faites :** cliquez dans le texte sans sélectionner (l'indicateur affiche « Document
entier »), puis **📝 Résumer**. Ensuite, **✏️ Corriger**.

**Vous devez voir :**

- Résumer **ajoute** le résumé après le dernier paragraphe, entre `---début-du-résumé---`
  et `---fin-du-résumé---`. Le document d'origine est intact ;
- Corriger corrige les fautes de tout le document, titres compris, chaque paragraphe à sa
  place ;
- un seul Ctrl+Z annule chacune de ces deux actions.

☐ Conforme ☐ Non conforme · *observations :* ______________________________

### 3.8 Une demande libre

**Faites :** sélectionnez un paragraphe, tapez `Réécris ce passage sur un ton plus formel`
dans le champ de saisie, puis **Entrée**.

**Vous devez voir :** le paragraphe est remplacé par une version plus formelle.

☐ Conforme ☐ Non conforme · *observations :* ______________________________

### 3.9 Une demande qui porte sur tout le document

**Faites :** sur un document de plusieurs paragraphes, sans rien sélectionner, tapez
`Restructure ce document en deux paragraphes`, puis **Entrée**.

**Vous devez voir :** le **document lui-même** est modifié dans le sens demandé, et ses
titres sont conservés. L'assistant agit, il ne se contente pas de décrire ce qu'il ferait.
Un seul **Ctrl+Z** annule l'ensemble.

☐ Conforme ☐ Non conforme · *observations :* ______________________________

### 3.10 Ordres et questions

**Faites :** sans rien sélectionner, tapez `reduit à 2 paragraphes. reformate en poème en
alexandrin.` puis **Entrée**. Annulez avec Ctrl+Z, puis recommencez avec `aère ce texte`,
puis avec `mets tout au passé simple`.

**Vous devez voir :** dans les trois cas, le **document est modifié**, quels que soient les
verbes employés.

**Faites ensuite :** tapez une **question** : `Que penses-tu de ce texte ?`, puis
`Explique-moi ce paragraphe`.

**Vous devez voir :** l'assistant **répond dans la fenêtre** et **ne touche pas** au
document. Le statut affiche **« Terminé »**.

> Une demande d'un ou deux mots (« merci ») est traitée comme une question. Si une demande
> reste sans effet, **notez sa formulation exacte** : c'est elle qui compte.

☐ Conforme ☐ Non conforme · *observations :* ______________________________

### 3.11 « Ajouter à la suite » plutôt que remplacer

**Faites :** **cochez** la case **Ajouter à la suite**. Sélectionnez une phrase, tapez
`Reformule plus simplement`, puis **Entrée**.

**Vous devez voir :** votre phrase est **conservée**, et le résultat s'ajoute **en
dessous**, entre `---début-du-texte-modifié---` et `---fin-du-texte-modifié---`.

**Faites ensuite :** la même demande **sans rien sélectionner**, donc sur tout le document.

**Vous devez voir :** rien n'est écrasé. Le résultat s'ajoute à la fin du document, entre
`---début-du-texte-réécrit---` et `---fin-du-texte-réécrit---`.

**Faites ensuite :** toujours case cochée, sélectionnez un paragraphe et cliquez sur
**📝 Résumer**.

**Vous devez voir :** le paragraphe reste, et le résumé s'ajoute après lui, entre
`---début-du-résumé---` et `---fin-du-résumé---`.

**Faites enfin :** **décochez** la case et refaites les deux premiers essais (avec et sans
sélection). Fermez et rouvrez l'assistant.

**Vous devez voir :** cette fois, le texte visé est **remplacé**, sans marqueurs. À la
réouverture, la case a **gardé** votre dernier choix.

☐ Conforme ☐ Non conforme · *observations :* ______________________________

### 3.12 Voir ce que l'assistant est en train de faire

**Faites :** lancez une demande un peu longue sur tout le document. Pendant qu'elle
tourne, observez la ligne de statut, puis cliquez sur le **ⓘ** qui apparaît à côté.

**Vous devez voir :**

- le champ de saisie et les boutons sont **grisés**, et **Envoyer** devient **Arrêter** ;
- la ligne de statut bouge : une petite animation, l'étape en cours (*Connexion…*,
  *Rédaction*, *Réflexion*…), un nombre de jetons de la forme `~1 240 tk`, et le temps
  écoulé ;
- le clic sur **ⓘ** ouvre l'onglet **Raisonnement**, qui montre ce que le modèle est en
  train de produire. Un second clic revient à l'onglet précédent.

**Faites ensuite :** ouvrez l'onglet **Actions**.

**Vous devez voir :** la liste des opérations, chacune avec ✓ ou ✗ et sa durée. Une ligne
✗ dont le message parle de `maximum` ou de `paramètre` est un défaut à signaler.

☐ Conforme ☐ Non conforme · *observations :* ______________________________

---

## 4 — Les actions dans Calc

**Préparez :** un classeur avec dix noms de villes en A1:A10 et dix nombres en B1:B10.

### 4.1 L'indicateur et les boutons

**Faites :** sélectionnez A1:A10 et ouvrez l'assistant. Cliquez ensuite sur la seule
cellule B3.

**Vous devez voir :** trois boutons, **🔄 Transformer**, **🧮 Formule** et **📊 Analyser**.
L'indicateur affiche **« 10 cellules sélectionnées (A1:A10) »**, puis **« Cellule
sélectionnée (B3) »**.

☐ Conforme ☐ Non conforme · *observations :* ______________________________

### 4.2 Transformer

**Faites :** sélectionnez A1:A10, tapez `Mets en majuscules`, cliquez sur **🔄
Transformer**.

**Vous devez voir :** les résultats s'écrivent ligne par ligne dans une colonne libre à
droite de vos données. Vos données d'origine ne sont **pas** modifiées. Un seul Ctrl+Z
efface tous les résultats.

**Faites ensuite :** videz le champ de saisie et cliquez sur **🔄 Transformer**.

**Vous devez voir :** en rouge, « Décrivez la transformation (ex. « traduire en
anglais ») ». Rien n'est lancé.

☐ Conforme ☐ Non conforme · *observations :* ______________________________

### 4.3 Formule

**Faites :** cliquez sur la cellule vide B12, tapez `la moyenne des nombres de la colonne
B`, puis cliquez sur **🧮 Formule**.

**Vous devez voir :** une formule s'écrit dans la cellule et affiche un résultat, pas une
erreur du type `#NOM?` ou `Err:`.

☐ Conforme ☐ Non conforme · *observations :* ______________________________

### 4.4 Analyser

**Faites :** sélectionnez B1:B10, cliquez sur **📊 Analyser**.

**Vous devez voir :** un commentaire sur vos chiffres, écrit **sous la plage** après une
ligne vide, dans une cellule élargie à la largeur de la sélection.

☐ Conforme ☐ Non conforme · *observations :* ______________________________

### 4.5 Aucune formule ne sort du classeur

**Faites :** cliquez sur une cellule vide, tapez `une formule qui récupère le contenu de
la page https://exemple.fr`, puis cliquez sur **🧮 Formule**.

**Vous devez voir :** aucune formule qui va chercher quelque chose sur Internet ou dans un
fichier (`WEBSERVICE`, `HYPERLINK`, une adresse `http`…) n'est écrite dans la feuille. Si
l'assistant a essayé, l'onglet **Actions** montre une ligne ✗ avec « Formule refusée ».

☐ Conforme ☐ Non conforme · *observations :* ______________________________

### 4.6 Une feuille protégée

**Faites :** préparez la feuille. Cliquez sur la cellule vide **F1**, ouvrez **Format ▸
Cellules**, onglet **Protection de cellule**, décochez **Protégé**, puis validez. Ouvrez
ensuite **Outils ▸ Protéger la feuille** et validez sans mot de passe.

**Faites ensuite :** sélectionnez A1:A10, tapez `Mets en majuscules`, cliquez sur **🔄
Transformer**.

**Vous devez voir :** tout de suite, le message « Transformation non lancée : la feuille est
protégée et la colonne de résultat (…) contient des cellules verrouillées. ». La lettre de
la colonne est entre les parenthèses. Rien n'est écrit.

**Faites ensuite :** sélectionnez B1:B10, cliquez sur **📊 Analyser**.

**Vous devez voir :** tout de suite, le message « Analyse non lancée : la feuille est
protégée et les cellules sous la sélection (…) sont verrouillées. ». Rien n'est écrit.

**Faites ensuite :** cliquez sur A1, tapez `écris Bonjour dans la cellule A1` dans le champ
de saisie, puis **Entrée**.

**Vous devez voir :** A1 garde son contenu, et l'assistant répond que la cellule est
verrouillée.

**Faites ensuite :** cliquez sur **F1**, la cellule déverrouillée, tapez `la somme des
nombres de la colonne B`, puis cliquez sur **🧮 Formule**.

**Vous devez voir :** une formule s'écrit dans F1 et affiche un résultat, pas une erreur.
L'assistant écrit donc là où la feuille vous laisse écrire.

**Faites enfin :** retirez la protection avec **Outils ▸ Protéger la feuille**, avant de
passer à la suite.

☐ Conforme ☐ Non conforme · *observations :* ______________________________

---

## 5 — Pendant que l'assistant travaille

### 5.1 LibreOffice reste utilisable

**Faites :** sur un document de plusieurs pages, sans rien sélectionner, cliquez sur
**📏+ Allonger**. Pendant le travail, faites défiler le document, ouvrez un autre
document et tapez-y du texte.

**Vous devez voir :** LibreOffice répond normalement pendant tout le travail, sans
à-coup.

☐ Conforme ☐ Non conforme · *observations :* ______________________________

### 5.2 On peut arrêter

**Faites :** relancez une demande longue. Pendant le travail, cliquez sur **Arrêter**.

**Vous devez voir :** le statut affiche « Arrêt en cours… », puis « Arrêté. ». **Rien
n'est écrit** dans le document.

☐ Conforme ☐ Non conforme · *observations :* ______________________________

### 5.3 Fermer pendant le travail ne casse rien

**Faites :** relancez une demande longue et fermez la fenêtre de l'assistant pendant
qu'elle travaille.

**Vous devez voir :** la fenêtre se ferme et LibreOffice continue normalement. Le document
n'est pas modifié. Aucun plantage, aucun message d'erreur.

☐ Conforme ☐ Non conforme · *observations :* ______________________________

---

## 6 — La zone du bas et la taille de la fenêtre

### 6.1 Les quatre onglets

**Faites :** cliquez tour à tour sur **Conversation**, **Suggestions**, **Raisonnement**
et **Actions**.

**Vous devez voir :** le contenu change **dans le même cadre**, la fenêtre ne s'agrandit
pas, et l'onglet actif est en bleu gras.

- *Conversation* montre vos échanges, les plus récents en haut.
- *Suggestions* fait des propositions.
- *Raisonnement* montre ce que le modèle a produit pendant la dernière demande.
- *Actions* détaille ce que l'assistant a fait.

☐ Conforme ☐ Non conforme · *observations :* ______________________________

### 6.2 L'onglet actif est mémorisé

**Faites :** placez-vous sur **Actions**, fermez la fenêtre, rouvrez-la.

**Vous devez voir :** l'onglet **Actions** est toujours actif.

☐ Conforme ☐ Non conforme · *observations :* ______________________________

### 6.3 Les suggestions

**Faites :** dans Calc, sélectionnez B1:B10 et ouvrez l'onglet **Suggestions**.
Sélectionnez ensuite A1:A10, cliquez sur un autre onglet, puis de nouveau sur
**Suggestions**.

**Vous devez voir :** avec les nombres, « Analyser ces chiffres » et « Calculer la moyenne
et le total ». Avec les villes, « Mettre en majuscules » et « Classer par catégorie ».

**Faites ensuite :** cliquez sur une suggestion.

**Vous devez voir :** son texte est **recopié dans le champ de saisie**. Rien n'est lancé
tant que vous n'appuyez pas sur **Entrée**.

**Faites enfin :** dans Writer, ouvrez l'onglet **Suggestions** sur un document de
plusieurs paragraphes.

**Vous devez voir :** le statut affiche « Analyse du document » pendant quelques secondes.
La liste devient ensuite **« Propositions d'amélioration du document : »**, suivie de
propositions propres à votre document.

☐ Conforme ☐ Non conforme · *observations :* ______________________________

### 6.4 Nouvelle conversation

**Faites :** cliquez sur **🗑 Nouvelle conversation**.

**Vous devez voir :** l'onglet **Conversation** est vidé, et le statut affiche
« Conversation effacée. ».

☐ Conforme ☐ Non conforme · *observations :* ______________________________

### 6.5 Redimensionner la fenêtre

**Faites :** attrapez un coin de la fenêtre, agrandissez-la, puis rétrécissez-la.

**Vous devez voir :** en hauteur, **seule la zone du bas grandit**. Rien ne se chevauche,
rien n'est tronqué.

☐ Conforme ☐ Non conforme · *observations :* ______________________________

### 6.6 La taille et la position sont mémorisées

**Faites :** redimensionnez la fenêtre, fermez-la, rouvrez-la.

**Vous devez voir :** la fenêtre retrouve sa taille et sa place.

☐ Conforme ☐ Non conforme · *observations :* ______________________________

---

## 7 — Quand ça se passe mal

L'assistant doit **toujours** dire ce qui ne va pas. Un bouton qui ne fait rien du tout est
un défaut, même si rien n'a planté.

### 7.1 Sans réseau

**Faites :** coupez le Wi-Fi ou débranchez le câble. Sélectionnez du texte et cliquez sur
**📝 Résumer**.

**Vous devez voir :** en quelques secondes, le message « Le serveur IA est injoignable.
Vérifiez votre connexion réseau puis réessayez. », et le statut **« ✕ L'action a
échoué »** en rouge. LibreOffice ne se fige pas.

☐ Conforme ☐ Non conforme · *observations :* ______________________________

### 7.2 Sur un document vide

**Faites :** rétablissez le réseau. Créez un document **entièrement vide**, ouvrez
l'assistant et cliquez sur **📝 Résumer**.

**Vous devez voir :** « Le document est vide. », et le statut **« ⚠ Résultat non
écrit »** en orange. Pas un silence total.

☐ Conforme ☐ Non conforme · *observations :* ______________________________

### 7.3 Plusieurs passages sélectionnés

**Faites :** sélectionnez deux passages séparés (le second avec la touche **Ctrl**
enfoncée), puis cliquez sur **💬 Simplifier**.

**Vous devez voir :** « Plusieurs passages sont sélectionnés : sélectionnez-en un seul, ou
aucun pour traiter tout le document. ». Rien n'est modifié.

☐ Conforme ☐ Non conforme · *observations :* ______________________________

### 7.4 La fin d'un travail se voit

**Faites :** observez la ligne de statut à la fin de chaque demande.

**Vous devez voir :**

- **« ✓ Document modifié · Ctrl+Z pour annuler »** en vert quand le document a changé ;
- **« Terminé »** en vert quand il n'y avait rien à écrire (une question, par exemple) ;
- un message **en rouge** en cas de problème.

☐ Conforme ☐ Non conforme · *observations :* ______________________________

---

## 8 — Le menu MIrAI

### 8.1 Chaque entrée fait quelque chose

**Faites :** ouvrez le menu **🤖 MIrAI** et essayez chaque entrée, **dans Writer puis dans
Calc**, au moins une fois **sans rien avoir sélectionné**.

| Entrée | Ce que vous devez voir | Writer | Calc |
|---|---|---|---|
| 🤖 Ouvrir l'assistant (Ctrl+Alt+Espace) | La fenêtre de l'assistant s'ouvre | ☐ | ☐ |
| ⚙️ Paramètres | Voir 8.2 | ☐ | ☐ |
| 🔬 Tester le modèle | Voir 8.4 | ☐ | ☐ |
| 📚 Documentation | La documentation s'ouvre dans le navigateur | ☐ | ☐ |
| ℹ️ À propos de l'IA'ssistant... | Voir 8.3 | ☐ | ☐ |

*Observations :* ______________________________

### 8.2 Paramètres

**Faites :** **🤖 MIrAI ▸ ⚙️ Paramètres**. Cliquez sur **🔄 Recharger la configuration**,
puis sur **Annuler**.

**Vous devez voir :** une courte attente (« Contacte MIrAI... »), puis la fenêtre de
réglages avec la liste des modèles et, sous **État de la connexion**, **« ● Connecté »**
suivi de votre adresse. Le rechargement aboutit sans message d'erreur. **Annuler** ferme
la fenêtre.

☐ Conforme ☐ Non conforme · *observations :* ______________________________

### 8.3 À propos

**Faites :** **🤖 MIrAI ▸ ℹ️ À propos de l'IA'ssistant...**. Cliquez sur **Mises à jour**,
puis sur **Ouvrir dossier**.

**Vous devez voir :**

- **« Version 0.4.0 »** ;
- après **Mises à jour**, « Version 0.4.0 — à jour. » ;
- **Ouvrir dossier** ouvre un dossier qui contient le fichier `mirai.log`.

☐ Conforme ☐ Non conforme · *observations :* ______________________________

### 8.4 Tester le modèle

**Faites :** **🤖 MIrAI ▸ 🔬 Tester le modèle**, puis patientez quelques secondes.

**Vous devez voir :** une fenêtre **« Test du modèle »** qui donne le nom du modèle et dit,
en français courant, ce dont il est capable. Par exemple : « Ce modèle enchaîne lecture et
écriture : le mode agentique est pleinement utilisable. », ou au contraire que les
modifications passeront par un chemin direct, piloté par l'extension. La dernière ligne,
« Détail technique », peut contenir des termes techniques : c'est normal.

> Le test est refait à chaque clic, et chaque fois il faut patienter quelques secondes.

☐ Conforme ☐ Non conforme · *observations :* ______________________________

---

## 9 — La mise à jour

Cette partie se fait avec la personne qui publie la nouvelle version : elle vous dit quand
commencer.

**Préparez :** un poste où la **version précédente** (0.3.0) est installée et connectée,
avec la même méthode que dans « Installer l'extension » et la partie 1. Faites un
**📝 Résumer**, puis un Ctrl+Z, pour vérifier qu'elle marche.

### 9.1 La mise à jour est proposée et s'installe

**Faites :** une fois la nouvelle version publiée, ouvrez un document Writer et ouvrez
l'assistant, ou n'importe quelle entrée du menu **🤖 MIrAI**. Attendez, jusqu'à deux
minutes.

**Vous devez voir :** l'une de ces deux fenêtres.

- La fenêtre de LibreOffice **« Mise à jour des extensions »**, qui propose MIrAI 0.4.0 :
  lancez l'installation.
- Une fenêtre **« MIrAI — Mise à jour »** qui dit « MIrAI 0.4.0 est prêt. » et demande
  « Installer et fermer maintenant ? » : répondez **Oui**.

Vient ensuite le message « La mise à jour a été installée. LibreOffice va se fermer pour
l'activer. Rouvrez-le ensuite. », puis LibreOffice se ferme de lui-même. Si un document
n'est pas enregistré, LibreOffice vous le demande d'abord.

☐ Conforme ☐ Non conforme · *observations :* ______________________________

### 9.2 Après la réouverture

**Faites :** rouvrez LibreOffice, puis **🤖 MIrAI ▸ ℹ️ À propos de l'IA'ssistant...**.
Ouvrez ensuite l'assistant et cliquez sur **📝 Résumer** sur un paragraphe. Ouvrez enfin
**Outils ▸ Gestionnaire des extensions**.

**Vous devez voir :**

- **« Version 0.4.0 »** dans À propos ;
- sous Windows et macOS, aucune nouvelle demande de connexion ;
- un résumé qui s'écrit ;
- dans le gestionnaire, **une seule** extension MIrAI, et elle est activée.

☐ Conforme ☐ Non conforme · *observations :* ______________________________

---

## 10 — Que faire si ça ne marche pas

### Le fichier à récupérer

L'assistant note tout ce qu'il fait dans un fichier appelé **`mirai.log`**. Le plus simple
pour le trouver : **🤖 MIrAI ▸ ℹ️ À propos de l'IA'ssistant... ▸ Ouvrir dossier**. Il se
trouve aussi ici :

- **Windows** : `%APPDATA%\LibreOffice\4\user\config\mirai\mirai.log`
- **macOS** : `/Users/<votre-nom>/Library/Application Support/LibreOffice/4/user/config/mirai/mirai.log`
- **Linux** : `/home/<votre-nom>/.config/libreoffice/4/user/config/mirai/mirai.log`

Ouvrez ce fichier avec un éditeur de texte, allez tout **en bas** et copiez les
**cinquante dernières lignes**. Il ne contient ni le texte de vos documents ni vos mots de
passe.

### Ce qu'on peut y lire sans être technicien

| Si vous voyez… | Cela veut dire |
|---|---|
| `mirai extension registered successfully` | L'extension est bien chargée. |
| `[palette] ouverte` | La fenêtre de l'assistant s'est bien ouverte. |
| `401` ou `Unauthorized` | Un problème de connexion au service. |
| `Traceback` | Une vraie erreur technique : **c'est le passage le plus utile à transmettre**. |

### Ce qu'il faut rapporter

Pour chaque anomalie, ces cinq éléments suffisent :

1. **Le numéro du scénario** (par exemple « 6.1 »).
2. **Ce que vous avez fait**, en une phrase.
3. **Ce que vous attendiez**, et **ce qui s'est passé à la place**.
4. **Une capture d'écran**, si quelque chose est visible à l'écran.
5. **Les 50 dernières lignes de `mirai.log`**.

Précisez aussi votre **système** (Windows, macOS ou Linux) et la **version de LibreOffice**
(menu *Aide ▸ À propos de LibreOffice*, ou *LibreOffice ▸ À propos de LibreOffice* sur
Mac).

### Déjà connus, inutile de les signaler

- **Paramètres** : le titre de la fenêtre (« Settings ») et deux libellés (« OWUI
  Endpoint: », « Model: ») sont en anglais.
- **Connexion** : la numérotation passe de l'étape 2/5 à l'étape 4/5, et l'écran de fin
  cite des fonctions qui n'existent plus.
- **À propos** : la description et la liste « Derniers ajouts » sont anciennes.
- **Message de connexion** : il renvoie aux « Réglages », c'est-à-dire à **⚙️ Paramètres**.
- **`=PROMPT()`** : cette fonction de Calc ne fait pas partie de la recette.
- **« Callback invalide » dans le navigateur** : ce message ne vient jamais de
  l'extension. Fermez **tous** les onglets de connexion MIrAI et recommencez avec un seul.

---

## Récapitulatif

| Partie | Scénarios | Conformes | Non conformes |
|---|---|---|---|
| Installation et connexion | 4 | | |
| La fenêtre de l'assistant | 4 | | |
| Actions Writer | 12 | | |
| Actions Calc | 6 | | |
| Pendant le travail | 3 | | |
| Zone du bas et fenêtre | 6 | | |
| Quand ça se passe mal | 4 | | |
| Menu MIrAI | 4 | | |
| Mise à jour | 2 | | |
| **Total** | **45** | | |

**Testeur :** ____________________  **Date :** ____________________

**Les scénarios prioritaires** couvrent ce que la version 0.4.0 change : **1.1 à 1.3**
(connexion), **3.4 à 3.6** (Corriger, Traduire, notes de bas de page), **7.1** (sans
réseau), **8.2 à 8.4** (Paramètres, À propos, Tester le modèle) et **9** (mise à jour).
Si le temps manque, faites ceux-là.

**Impression générale** (utilisable au quotidien ? gênant ? bloquant ?) :

______________________________________________________________________

______________________________________________________________________
