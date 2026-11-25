# Mise à jour du plugin — contrat & fonctionnement

Réfs : issue plugin [#9](https://github.com/IA-Generative/AssistantMiraiLibreOffice/issues/9)
(fiabilité de bout en bout).

## Vue d'ensemble — deux routes, un seul point d'installation

Le DM décide (directive `update` ou `rollback` servie par `/config`), le plugin
télécharge et installe. L'installation passe par **`ExtensionManager.addExtension`
exécuté sur le thread principal de soffice** — le code exact du Gestionnaire des
extensions, la seule voie validée comme fiable sur plusieurs cycles. Aucune route
ne spawne de processus enfant (immunisé WinError 5 / AppLocker / Defender ASR).

| Route | Déclencheur | Téléchargement + installation | Cohorte/canary |
|---|---|---|---|
| **1. Dirigée** | Directive `update` ou `rollback` du DM | Plugin : download failover + sha256 → `addExtension` main-thread → fermeture propre | ✅ oui |
| **2. Manuelle** (fallback validé GPO) | Échec de 1 | L'utilisateur double-clique l'OXT stagé → Gestionnaire des extensions | ✅ (directive) |

L'extension ne déclare pas de feed `<update-information>` : le module de mise à
jour en ligne de LibreOffice est absent ou désactivé sur le parc, la mise à jour
passe donc par le plugin.

**Fermeture après installation.** La fermeture est retentée jusqu'à
acceptation : LibreOffice la refuse (veto) tant qu'une de ses fenêtres modales
est ouverte, ou quand son thread principal est occupé. Deux vetos sont au
contraire des **refus humains**, respectés sans nouvel essai : un veto
alors qu'un document ouvert porte des modifications non enregistrées
(`isModified()` sur les composants du Desktop, sondés sur le thread principal)
— il vient du dialogue « Enregistrer les modifications ? » —, et un veto arrivé
après plus d'une seconde passée dans `terminate()`. Chaque abandon affiche une
boîte courte, « La mise à jour s'activera au prochain démarrage de
LibreOffice. » : la boîte précédente promettait une fermeture qui n'a pas eu
lieu. **SIGTERM** ne reste qu'un dernier recours quand le thread principal est
*injoignable* (callback ou `AsyncCallback` indisponible, `addCallback` en
échec) ; une exception remontée par l'action — Desktop indisponible, service en
cours de disposition — prouve au contraire qu'il répond et n'y donne pas droit.

**Refus et reports.** Un « Non » à la boîte « MIrAI {version} est prêt » pose
`postponed_until` dans `pending_update/update_state.json` : la cible n'est pas
reproposée avant 24 h, et rien n'est retéléchargé entre-temps.

## Vérification sur poste (checklist qualif)

1. Installer une version N, publier N+1 côté DM (campagne visant ce poste).
2. Répondre « Oui » à la boîte « MIrAI {version} est prêt… Installer et fermer
   maintenant ? » ; répéter 3 cycles consécutifs : l'extension doit rester
   visible et fonctionnelle (pas d'entrées fantômes dans
   `registrymodifications.xcu`).
3. Après « Oui » : observer la boîte « installée, LibreOffice va se fermer » du
   plugin, et vérifier si la fermeture est refusée
   (`grep -E "terminate (accepted|vetoed)|veto persistant|refusée par l'utilisateur|fermeture impossible" <profil>/user/config/mirai/mirai.log`).
   Variante à jouer : laisser un document modifié ouvert et répondre « Annuler »
   à « Enregistrer les modifications ? » — un seul dialogue doit apparaître,
   suivi de la boîte « s'activera au prochain démarrage » (ni réessai, ni SIGTERM).

## Fiabilité de la route dirigée

- **Install sur le main thread via `ExtensionManager.addExtension`**, sans
  remove-avant-add : le remplacement même-identifiant est atomique
  (`VersionException` auto-approuvée). C'est la seule voie d'installation du
  plugin : si elle échoue, la route manuelle prend le relais. L'API bas niveau
  `removePackage`/`addPackage` n'est pas utilisée : elle ajoute le paquet sans
  l'enregistrer et laisse l'ancien enregistrement orphelin.
- **Rapport DM véridique** : `deferred` au staging, `installed` seulement quand
  la nouvelle version est **réellement active** (réconciliation au démarrage
  suivant, `_reconcile_update_state`), qui purge aussi `pending_update` et lève
  l'anti-boucle. Un état périmé (> 14 j) est purgé silencieusement.
- **Installation qui n'a jamais pris effet, tranchée au démarrage** : une étape
  `installed_inprocess` dont la cible n'est pas active au redémarrage
  (enregistrement annulé par LibreOffice, dossier de cache laissé derrière)
  est rapportée `failed` au DM puis purgée — au lieu d'attendre la
  purge « périmée » à 14 jours pendant lesquels le poste ne retentait rien. En
  session, le registre garde l'ancienne version jusqu'au redémarrage : la même
  réconciliation, lancée par le worker, n'en conclut rien.
- **Une seule campagne en attente de redémarrage** : tant qu'une installation
  attend son redémarrage, une nouvelle directive est ignorée (avec une ligne de
  log) au lieu d'écraser l'état persistant — sinon la campagne installée ne
  serait jamais rapportée `installed`.
- **Une installation encore en cours sur le thread principal n'est pas un
  échec** : l'étape `installed_inprocess` est persistée et la réconciliation
  tranche au redémarrage ; ni rapport `failed`, ni cible bannie, ni boîte
  manuelle — qui inviterait à une seconde installation concurrente.
- **Pas de re-exec** : fermeture propre de LO (main thread), réouverture par
  l'utilisateur — comportement validé sur toutes les plateformes.

## Observabilité — mesurer au lieu de supposer

- **Télémétrie par étape** du flux piloté DM : `UpdateStaged` → `UpdateAccepted`
  / `UpdatePostponed` → `UpdateInstalledPendingRestart` / `UpdateInstallFailed`
  → `ExtensionUpdated` (`confirmed:true`, émis à la réconciliation quand la
  nouvelle version est réellement active). Le taux de succès de la cohorte se
  lit par étape, l'entonnoir montre où ça casse.

## Vérifier la sémantique deferred→installed contre le DM (sans le modifier)

Le plugin rapporte `deferred` au staging puis `installed` après
redémarrage. Pour vérifier que les campagnes DM digèrent ce cycle en deux
temps (progression correcte, pas d'expiration entre les phases) :

```bash
python3 tests/simulation/deploy_simulator.py \
  --devices 100 --bootstrap-url https://<bootstrap> --campaign-id <id> \
  --relay-client <rc> --relay-key <rk> \
  --restart-delay 5 --admin-token $DM_ADMIN_TOKEN
```

Le rapport affiche le progrès de campagne avant/après, compte les devices
`stuck_in_deferred` (cycle cassé) et `--single-phase` simule un plugin qui
rapporte `installed` dès le staging. `/update/status` exige les
relay-credentials : sans `--relay-client`/`--relay-key`, un DM sécurisé répond
401.
