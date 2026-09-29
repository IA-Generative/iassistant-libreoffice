# Mise à jour native LibreOffice (`<update-information>`) — contrat & fonctionnement

Réfs : issue plugin [#5](https://github.com/IA-Generative/AssistantMiraiLibreOffice/issues/5)
(brancher le mécanisme natif), issue plugin [#9](https://github.com/IA-Generative/AssistantMiraiLibreOffice/issues/9)
(fiabilité de bout en bout), feed côté DM : IA-Generative/device-management#4.

## Vue d'ensemble — trois routes, un seul point d'installation

Toutes les routes convergent vers **`ExtensionManager.addExtension` exécuté sur le
thread principal de soffice** — le code exact du Gestionnaire des extensions, la
seule voie validée comme fiable sur plusieurs cycles. Aucune route par défaut ne
spawne de processus enfant (immunisé WinError 5 / AppLocker / Defender ASR).

| Route | Déclencheur | Téléchargement + installation | Cohorte/canary |
|---|---|---|---|
| **1. Native pilotée** (principale) | Directive `update` du DM, si le feed installé annonce exactement `target_version` | LibreOffice : dialogue « Mise à jour des extensions » ouvert par le plugin via `PackageManagerDialog.trigger("SHOW_UPDATE_DIALOG")`, download depuis le feed, `addExtension` | ✅ sur le déclenchement (le feed, lui, est anonyme) |
| **2. Dirigée** (repli) | Directive `update` ou `rollback` ; feed absent, injoignable ou divergent ; urgence `deferred` ; deux tentatives natives sans installation | Plugin : download failover + sha256 → `addExtension` main-thread → fermeture propre | ✅ oui |
| **3. Manuelle** (fallback validé GPO) | Échec de 2 | L'utilisateur double-clique l'OXT stagé → Gestionnaire des extensions | ✅ (directive) |

Point établi en vérifiant les sources LibreOffice (`desktop/source/deployment/gui/`) :
le service créable `com.sun.star.deployment.ui.PackageManagerDialog` implémente
`XJobExecutor`, et `trigger("SHOW_UPDATE_DIALOG")` ouvre directement le dialogue de
mise à jour des extensions — le chemin de la bulle de notification de LibreOffice
(`updatecheck.cxx`, `showExtensionDialog`). C'est ainsi que la directive DM produit
l'effet « push » sur la route native. Le bouton « Vérifier les mises à jour » du
Gestionnaire des extensions reste disponible au support, indépendamment du plugin.

**Fermeture après installation (route 1).** La fermeture est retentée jusqu'à
acceptation : LibreOffice la refuse (veto) tant que sa fenêtre de progression
est ouverte ; il ne propose pas lui-même de redémarrer sur ce chemin (l'invite
native n'existe qu'à la fermeture du Gestionnaire des extensions). Deux vetos
sont au contraire des **refus humains**, respectés sans nouvel essai : un veto
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

**Refus et reports.** Un « Non » (route 2), une annulation ou une version ignorée dans
le dialogue natif (route 1) posent `postponed_until` dans `pending_update/update_state.json` :
la cible n'est pas reproposée avant 24 h, et rien n'est retéléchargé entre-temps.

## Ce que le build bake dans `description.xml`

`scripts/inject_update_feed.py` (appelé par `02-build-oxt.sh`) ajoute au staging :

```xml
<update-information>
  <src xlink:href="https://<bootstrap-1>/catalog/mirai-libreoffice/update.xml"/>
  <src xlink:href="https://<bootstrap-2>/catalog/mirai-libreoffice/update.xml"/>
</update-information>
```

- Une entrée `<src>` par `bootstrap_urls` du profil embarqué — LibreOffice les
  essaie **dans l'ordre** (failover natif, même sémantique que le multi-bootstrap
  du plugin).
- Override build : `MIRAI_UPDATE_FEED_URL=<url>` (une seule entrée).
- Profil offline (`enabled: false`) : aucun bloc — le bouton natif répond
  « aucune mise à jour ».

## Contrat du feed `update.xml` (servi par le DM)

`GET <bootstrap>/catalog/mirai-libreoffice/update.xml` — anonyme, `Content-Type`
indifférent (`text/xml` recommandé). Namespace **obligatoire**
`http://openoffice.org/extensions/update/2006` :

```xml
<?xml version="1.0" encoding="UTF-8"?>
<description xmlns="http://openoffice.org/extensions/update/2006"
             xmlns:xlink="http://www.w3.org/1999/xlink">
  <identifier value="fr.gouv.interieur.mirai"/>
  <version value="0.0.1.0.32"/>
  <update-download>
    <src xlink:href="https://<bootstrap>/artifacts/mirai-libreoffice/0.0.1.0.32/mirai.oxt"/>
  </update-download>
</description>
```

- `identifier` : doit être strictement `fr.gouv.interieur.mirai` (sinon LO ignore le feed).
- `version` : la dernière version publiée du tier. LibreOffice compare segment
  par segment (numérique) — le schéma à 5 segments `0.0.1.0.NN` est géré.
  LO propose la MAJ ssi `version(feed) > version(installée)`.
- `update-download/src` : URL de l'OXT, téléchargée par LibreOffice lui-même
  (anonymement). Plusieurs `<src>` possibles (miroirs, essayés dans l'ordre).
- Optionnel : `<release-notes><src xlink:href="…" lang="fr"/></release-notes>`.

## Vérification sur poste (checklist qualif)

1. Installer une version N, publier N+1 côté DM (feed à jour).
2. Outils → Gestionnaire des extensions → **Vérifier les mises à jour** :
   la MAJ doit apparaître ; installer ; sur le chemin manuel LibreOffice propose
   le redémarrage à la fermeture du Gestionnaire ; sur le chemin piloté c'est le
   plugin qui ferme LibreOffice.
3. Répéter 3 cycles consécutifs : l'extension doit rester visible et
   fonctionnelle (pas d'entrées fantômes dans `registrymodifications.xcu`).
4. Points durs à valider sur poste durci MI :
   - **proxy** : LibreOffice récupère le feed avec sa propre pile HTTP
     (Options → Internet → Proxy, ou proxy système) — pas celle du plugin ;
   - **GPO « Mise à jour en ligne »** : si désactivée, plus de contrôle
     hebdomadaire ; restent le bouton manuel du Gestionnaire des extensions et
     le déclenchement par le plugin sur directive DM ;
   - certificats : la chaîne TLS du bootstrap doit être reconnue par LO.
5. Sonde Basic : `oDlg.trigger("SHOW_UPDATE_DIALOG")` doit **rendre la main
   avant** que le dialogue soit fermé (garantie empirique que le timeout du
   plugin reste théorique).
6. Après « Installer » dans le dialogue natif : observer la boîte « installée,
   LibreOffice va se fermer » du plugin pendant que le dialogue LibreOffice
   est encore ouvert, et vérifier si la fermeture est refusée
   (`grep -E "terminate (accepted|vetoed)|veto persistant|refusée par l'utilisateur|fermeture impossible" ~/log.txt`).
   Variante à jouer : laisser un document modifié ouvert et répondre « Annuler »
   à « Enregistrer les modifications ? » — un seul dialogue doit apparaître,
   suivi de la boîte « s'activera au prochain démarrage » (ni réessai, ni SIGTERM).
7. `grep "_native_feed_offers" ~/log.txt` sur le poste durci : `offers=True`
   prouve que la pile UCB de LibreOffice traverse proxy et TLS jusqu'au feed.
8. Deux sauts natifs consécutifs N → N+1 → N+2 avec inspection de
   `pending_update/update_state.json` entre les deux (`route`,
   `native_attempts`, purge après réconciliation), puis un « ignorer »
   volontaire deux fois pour voir la bascule en route dirigée à la troisième
   directive.

## Fiabilité route 2, dirigée

- **Install sur le main thread via `ExtensionManager.addExtension`**, sans
  remove-avant-add : le remplacement même-identifiant est atomique
  (`VersionException` auto-approuvée). Les cycles `removePackage`/`addPackage`
  bas niveau depuis le thread worker — la cause des entrées fantômes — sont
  relégués en tout dernier recours.
- **Scripts `.bat`/`.sh` désactivés par défaut** (spawn d'enfant = WinError 5 +
  cycle unopkg corrupteur). Réactivation diagnostic : `MIRAI_UPDATE_ALLOW_SCRIPT=1`.
- **Rapport DM véridique** : `deferred` au staging, `installed` seulement quand
  la nouvelle version est **réellement active** (réconciliation au démarrage
  suivant, `_reconcile_update_state`), qui purge aussi `pending_update` et lève
  l'anti-boucle. Un état périmé (> 14 j) est purgé silencieusement.
- **Installation qui n'a jamais pris effet, tranchée au démarrage** : une étape
  `installed_native` / `installed_inprocess` dont la cible n'est pas active au
  redémarrage (enregistrement annulé par LibreOffice, dossier de cache laissé
  derrière) est rapportée `failed` au DM puis purgée — au lieu d'attendre la
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
- **`NativeFeedCheck`** : au démarrage (~45 s, une fois par process, headless),
  le plugin interroge le feed via `com.sun.star.deployment.UpdateInformationProvider`
  — la machinerie exacte du bouton « Vérifier les mises à jour », donc la pile
  HTTP de LibreOffice (proxy/TLS/GPO propres à LO). Résultat en log +
  télémétrie : `feed.ok`, `feed.announced_version`, `feed.error`. C'est la
  validation à l'échelle de la flotte de la viabilité de la route native sur
  postes durcis, sans aucune action utilisateur — et l'alarme si le feed DM
  est absent ou mal formé.

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
