# Mise à jour native LibreOffice (`<update-information>`) — contrat & fonctionnement

Réfs : issue plugin [#5](https://github.com/IA-Generative/AssistantMiraiLibreOffice/issues/5)
(brancher le mécanisme natif), issue plugin [#9](https://github.com/IA-Generative/AssistantMiraiLibreOffice/issues/9)
(fiabilité de bout en bout), feed côté DM : IA-Generative/device-management#4.

## Vue d'ensemble — trois routes, un seul point d'installation

Toutes les routes convergent vers **`ExtensionManager.addExtension` exécuté sur le
thread principal de soffice** — le code exact du Gestionnaire des extensions, la
seule voie validée comme fiable sur plusieurs cycles. Aucune route ne spawne de
processus enfant (immunisé WinError 5 / AppLocker / Defender ASR).

| Route | Déclencheur | Téléchargement + installation | Cohorte/canary |
|---|---|---|---|
| **1. Native pilotée** (principale) | Directive `update` du DM, si le feed installé annonce exactement `target_version` et si le paquet installé n'exige pas de signature (voir « Signature du paquet servi ») | LibreOffice : dialogue « Mise à jour des extensions » ouvert par le plugin via `PackageManagerDialog.trigger("SHOW_UPDATE_DIALOG")`, download depuis le feed, `addExtension` | ✅ sur le déclenchement (le feed, lui, est anonyme) |
| **2. Dirigée** (repli) | Directive `update` ou `rollback` ; feed absent, injoignable ou divergent ; urgence `deferred` ; deux tentatives natives sans installation ; **toujours** quand le paquet installé exige une signature | Plugin : download failover + sha256 + signature → `addExtension` main-thread → fermeture propre | ✅ oui |
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

## Signature du paquet servi

Format et chaîne complète : ADR-0006 du Device Management (« chaîne de confiance
des paquets »). Côté plugin :

- **Ancre** : `dm-trust.json`, déposé par le DM à la racine du paquet qu'il sert.
  Il liste les clés racines (`dm-trust/1`).
- **Règle** : un plugin dont le paquet **installé** contient un `dm-trust.json`
  n'installe une mise à jour reçue par directive que si elle porte une enveloppe
  valide (`update.signature`, `dm-package-signature/1`). Sans ce fichier (paquet
  antérieur), le contrôle reste l'empreinte seule. L'exigence vient du paquet
  installé : un DM ne peut pas la lever à distance.
- **Déroulé** (`src/mirai/shell/update_signature.py`) : après le téléchargement
  et le contrôle de l'empreinte, avant toute mise en place du fichier. Vérifié :
  signature par une racine, ou par une clé couverte par une délégation valide
  (signée par une racine, dans ses dates) ; `kind == "served"` ; `sha256` des
  octets téléchargés ; version égale à la cible de la directive (le numéro du
  DM, `X.Y.Z-rc.N` pour une pré-version) ; puis le lien avec cette extension :
  l'`extension_id` de la déclaration doit être `fr.gouv.interieur.mirai`. Une
  déclaration sans `extension_id` (DM antérieur) est comparée au slug du chemin
  de configuration (`/config/<slug>/config.json`).
- **Refus** : rien n'est mis en place ni installé ; le refus part vers le DM
  (`POST /update/status`, statut `failed`, `error_detail = "signature: <raison>"`)
  et en télémétrie (`UpdateInstallFailed`, attribut `signature_reason`) ; la
  cible n'est plus retentée d'ici le redémarrage de LibreOffice. Raisons :
  `no_envelope`, `malformed`, `unknown_key`, `bad_signature`,
  `delegation_invalid`, `wrong_kind`, `digest_mismatch`, `version_mismatch`,
  `extension_mismatch`, `slug_mismatch`, `no_trust`.
- **Calcul** : le Python embarqué par LibreOffice n'a pas la bibliothèque
  `cryptography`. `src/mirai/package_signature.py` vérifie ES256 (ECDSA P-256,
  SHA-256, signature brute `r||s`) avec la bibliothèque standard seule. Ses
  vecteurs de test sont écrits par
  `tests/fixtures/generate_package_signature_vectors.py`, avec `cryptography`.

**La route native n'est pas couverte.** Sur les routes 1 et « Vérifier les mises à
jour », c'est LibreOffice qui télécharge et installe : le plugin ne voit pas les
octets, rien n'y vérifie la signature, et la confiance n'y repose que sur TLS et
sur l'adresse du flux inscrite dans `description.xml`. Deux conséquences :

- un paquet qui exige une signature **n'ouvre plus lui-même** le dialogue natif
  sur directive : il passe toujours par la route dirigée, qui vérifie ;
- le bouton « Vérifier les mises à jour » du Gestionnaire des extensions et la
  vérification périodique de LibreOffice restent hors de portée du plugin. Tant
  que le paquet porte un bloc `<update-information>`, ils installent ce que le
  flux annonce, sans signature. Fermer ce canal relève du paquet servi (ne plus
  y inscrire de flux) ou du poste (désactiver la mise à jour en ligne des
  extensions), pas du plugin.

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
  LO propose la MAJ ssi `version(feed) > version(installée)`. Une pré-version
  (`X.Y.Z-rc.N`, `X.Y.Z-dev.N`) y est annoncée dans l'écriture numérique de son
  `description.xml`, jamais avec son suffixe, que LibreOffice range après la
  version finale : voir [pre-versions.md](pre-versions.md).
- `update-download/src` : URL de l'OXT, téléchargée par LibreOffice lui-même
  (anonymement). Plusieurs `<src>` possibles (miroirs, essayés dans l'ordre).
- Optionnel : `<release-notes><src xlink:href="…" lang="fr"/></release-notes>`.

## Réécriture de l'adresse du feed

Sur l'adresse nue du feed, le DM sert la **version générale** du plugin (celle
destinée à tout le parc) ; `?version=X` ne confirme que X (404 si le DM ne peut
pas la servir).
Pour que la route native suive la cohorte, le plugin réécrit l'adresse dans le
`description.xml` de **son installation** (`src/mirai/feed_rewrite.py`) :

| Situation | Adresse écrite |
|---|---|
| Directive `update` vers X (même si déjà à X, même en report) | `…/update.xml?version=X` |
| Aucune directive, ou directive `rollback` | `…/update.xml?version=<version installée>` |

LibreOffice relit ce fichier à **chaque** vérification (code source LibreOffice :
`getUpdateInformationURLs` → lecture de `<dossier>/description.xml`). Le feed ne
confirme que la version demandée : chaque poste ne voit que sa propre cible, ni le
bouton « Vérifier les mises à jour » ni la vérification hebdomadaire ne diffusent
un canary au reste du parc.

- **Quand** : 2 s après le démarrage (avant le diagnostic à 45 s, et sans
  écraser une directive déjà lue), à chaque lecture de `/config` avant toute
  décision (donc avant `_native_feed_offers`).
- **Comment** : seul le paramètre `version` de chaque `<src>` change (ordre,
  autres paramètres et override `MIRAI_UPDATE_FEED_URL` conservés), écriture
  atomique et seulement si le contenu change, droits et fins de ligne du fichier
  conservés ; bloc absent (profil offline) → rien n'est créé.
- **Installation partagée** (dossier non inscriptible) : résultat `unwritable`,
  journalisé ; `_native_feed_offers` ne voit pas la cible et la route dirigée
  prend le relais. Sous Windows, un fichier verrouillé à cet instant (antivirus,
  EDR, lecture par LibreOffice) donne aussi `unwritable` : un `written` à une
  lecture de `/config` suivante signale un verrou passager.
- **Après une mise à jour** : le nouvel OXT arrive avec l'adresse nue du build
  (version générale) ; elle est reprise au démarrage suivant.
- **Télémétrie** `FeedRewrite` (une par changement d'état) : `feed.target`,
  `feed.result` (`written` / `unchanged` / `absent` / `unwritable` / `error`),
  `feed.error`. `NativeFeedCheck` interroge la même adresse.

## Vérification sur poste (checklist qualif)

0. **Réécriture de l'adresse**, à faire en premier :
   - après démarrage, ouvrir le `description.xml` installé
     (`<profil>/user/uno_packages/cache/uno_packages/<lu…>/<…>.oxt/`) : chaque
     `<src>` finit par `?version=<version installée>` ;
     `grep "_rewrite_feed_url" <profil>/user/config/mirai/mirai.log` → `written` puis plus rien tant que la
     cible ne change pas ;
   - sans directive, **Vérifier les mises à jour** ne propose rien, même si une
     version plus récente est publiée sur le DM ;
   - avec une directive vers N+1 (campagne visant ce poste), le fichier passe à
     `?version=N+1` et la mise à jour native est proposée ; un poste hors
     campagne ne voit toujours rien ;
   - antivirus / EDR : aucune alerte sur l'écriture du fichier ; un
     `unwritable` suivi d'un `written` est un verrou passager ;
   - installation en couche partagée (si utilisée sur le parc) :
     `feed.result=unwritable` et bascule en route dirigée.

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
   (`grep -E "terminate (accepted|vetoed)|veto persistant|refusée par l'utilisateur|fermeture impossible" <profil>/user/config/mirai/mirai.log`).
   Variante à jouer : laisser un document modifié ouvert et répondre « Annuler »
   à « Enregistrer les modifications ? » — un seul dialogue doit apparaître,
   suivi de la boîte « s'activera au prochain démarrage » (ni réessai, ni SIGTERM).
7. `grep "_native_feed_offers" <profil>/user/config/mirai/mirai.log` sur le poste durci : `offers=True`
   prouve que la pile UCB de LibreOffice traverse proxy et TLS jusqu'au feed.
8. Deux sauts natifs consécutifs N → N+1 → N+2 avec inspection de
   `pending_update/update_state.json` entre les deux (`route`,
   `native_attempts`, purge après réconciliation), puis un « ignorer »
   volontaire deux fois pour voir la bascule en route dirigée à la troisième
   directive.

## Fiabilité route 2, dirigée

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
