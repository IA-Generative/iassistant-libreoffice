# ADR-0002 : Décisions du moteur et de la palette

**Date** : 2026-11-04
**Statut** : Accepté
**Portée** : le moteur (`src/mirai/core/`), la palette (`src/mirai/ui/`) et
l'authentification des appels LLM.

---

## Contexte

Le moteur et la palette ont été construits sur un plan rangé dans `prompts/`, avec
une liste de pièges appris au banc. L'ADR 0001 retire ce dossier une fois ses
décisions reprises. La plupart de ses règles sont déjà écrites, avec leur raison,
dans CLAUDE.md, docs/ARCHITECTURE.md, docs/CONFIGURATIONS.md ou les docstrings du
code. Cet ADR garde celles dont la raison n'était écrite nulle part ailleurs.

## Décision

1. **Forme de l'assistant.** L'assistant est un registre de tools typés sur les API
   UNO, orchestré par le modèle et ouvert depuis la palette, son unique point
   d'entrée, aujourd'hui une fenêtre flottante non modale. Les fonctions fréquentes
   y sont des chips ; les actions de la coquille (Paramètres, Tester le modèle,
   Documentation, À propos) restent dans le menu 🤖 MIrAI. La palette est faite en
   contrôles UNO, l'API n'offrant aucune vue web : elle n'a ni coins arrondis ni
   ombres. Ses chips sont des `FixedText`, car les thèmes gtk3 et macOS imposent
   leurs couleurs aux boutons.
2. **Le `llmToken` seul sur `/llm/v1`.** Le plugin s'y authentifie par
   `Authorization: Bearer <llmToken>`, jamais par la paire relais, pour deux
   raisons :
   - la paire relais est le credential maître du poste (configuration, télémétrie,
     LLM) et vit 30 jours ; le `llmToken` ne vaut que pour le LLM et vit une heure ;
   - côté Device Management, la présence de `X-Relay-Client` engage la vérification
     relais, qui échoue en 401 sans repli vers le Bearer : des en-têtes relais
     périmés masqueraient un `llmToken` valide.

   La paire relais sert aux autres routes du Device Management (`/config`,
   `/telemetry`, `/update/status`…).
3. **Dégradation gracieuse.** Rien de ce qui est optionnel ou externe ne casse
   l'assistant : police Marianne, tool calling natif, bloc `usage` du relais,
   Device Management, `ruff` sur le poste. Chaque capacité a un repli, et son
   absence dégrade le service sans le bloquer.
4. **Une seule rangée de chips**, pour garder la palette compacte. Quand elle
   déborde, on retire une chip que la demande libre couvre ou on raccourcit un
   libellé ; le retour à la ligne de `_layout()` est un filet, pas une mise en page.
5. **Une couche ouverte sous un `except` large a un test qui la construit
   entière.** Sans lui, toute erreur de construction devient une fenêtre qui
   n'apparaît pas, sans message. C'est le cas de la palette, ouverte par
   `entry.open_palette` et construite par `tests/unit/core/test_palette_build.py`.
   Les faux UNO déclarent chaque interface servant de classe de base : une
   interface absente devient un `MagicMock`, et l'import échoue sur un « metaclass
   conflict », loin de la cause.
6. **Tout chemin d'exécution écrit dans l'onglet Actions** par `journal_line` :
   agentique, pipeline, réécriture de la sélection, réécriture du document. C'est
   là que l'utilisateur cherche ce qui vient de se passer, et un chemin muet laisse
   un onglet vide qu'on prend pour une panne d'affichage.
7. **Un contenu qu'on lit va dans un panneau, pas dans une infobulle.** Le survol
   (`HelpText`) ne porte qu'une précision courte. Un contenu long ou qui évolue en
   direct, comme le raisonnement du modèle, va dans un panneau persistant et
   défilable : l'infobulle disparaît au moindre mouvement de souris et ne défile
   pas.
8. **Un réglage visible est honoré par tous les chemins qu'il gouverne.** « Ajouter
   à la suite » vaut pour la sélection comme pour le document entier, sur les
   actions rapides comme sur la demande libre. Un réglage qui ne commande plus rien
   est pire qu'un réglage absent : l'utilisateur croit avoir agi.
9. **Un défaut latent dans du code voué à la suppression se neutralise tout de
   suite**, par une garde bon marché : tant que ce code est livré, le défaut reste
   atteignable, et son symptôme ne désigne jamais le coupable.
10. **Une factorisation de `core/` ou de `ui/` se fait avec la suite verte avant et
    après, sans toucher une assertion.** Un test qui doit changer signale un
    changement de comportement, donc une factorisation qui déborde. Comme pour la
    coquille (ADR 0001), une refacto du moteur ou de la palette commence par un
    état des lieux et un plan partagés.

## Conséquences

- `prompts/` est supprimé. Ses autres règles vivent dans CLAUDE.md,
  docs/ARCHITECTURE.md, docs/CONFIGURATIONS.md et les docstrings ; les méthodes de
  diagnostic qu'il décrivait sont dans docs/ARCHITECTURE.md, § Diagnostic.
- Une règle de cet ADR qui change passe par un nouvel ADR qui la remplace.
- Restent ouvertes, sans décision ici : la taille des polices de la palette (6 à
  8 pt, 9 pour Envoyer) et l'accueil de la palette dans le panneau latéral de
  LibreOffice.
