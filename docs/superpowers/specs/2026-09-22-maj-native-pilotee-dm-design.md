# Mise à jour du plugin pilotée par le DM, route native LibreOffice d'abord

Spec de conception, 2026-09-22. Issue de suivi : [#9](https://github.com/IA-Generative/AssistantMiraiLibreOffice/issues/9).
Sous-tâches liées : plugin #5 (feed natif), DM #4 (feed côté serveur), DM #5 (cache binaire).
Base de code plugin : branche `origin/fix/MAJ` (rperaudin, 2026-09-04 → 11, non fusionnée,
contient tout `develop`). Base DM : branche `main` de `../device-management`.

## 1. Objectif

Permettre au Device Management (DM) de mettre à jour l'extension LibreOffice sur les
postes du ministère, y compris les postes durcis où aucun processus enfant ni script ne
peut être lancé depuis LibreOffice, sans corrompre le registre d'extensions, en gardant
le pilotage DM des campagnes (cohortes, paliers canary, urgence, rollback).

## 2. Décisions actées avec Johann

| Sujet | Décision |
|---|---|
| Mise à jour en ligne de LibreOffice | Désactivée par GPO sur les postes : pas de contrôle hebdomadaire ni de bulle. Le déclenchement vient du plugin. |
| Scripts, processus enfants | Impossibles sur poste durci. Aucune route par défaut n'en lance. |
| Expérience utilisateur | Deux clics acceptés. Fermeture de LibreOffice acceptée, préférée si c'est le comportement natif. |
| Feed anonyme | Accepté : tout poste qui interroge un DM voit la même version. Le ciblage reste porté par la directive. |
| Code chargé depuis le profil utilisateur | Refusé, sauf dernier recours. Hors périmètre. |
| Ordre des routes | Native d'abord, dirigée en repli, manuel en dernier. |
| Base | Repartir de `fix/MAJ`. Implémentation DM sur une branche dédiée, PR ensuite. |

Condition posée par Johann : les directives de mise à jour et les cohortes cibles
restent pilotables depuis le DM. Réponse de cette spec : rien ne change dans la
résolution de campagne ni dans la directive ; seule la mécanique d'installation change,
et la route native n'est empruntée que lorsque le feed annonce exactement la version
cible de la directive. Le DM reste seul maître de la cible.

## 3. Architecture

Un seul primitif d'installation : `ExtensionManager.addExtension` exécuté sur le thread
principal de soffice, dans le processus. C'est l'appel que le Gestionnaire des extensions
et l'updater natif exécutent (vérifié dans `desktop/source/deployment/gui/dp_gui_updateinstalldialog.cxx`).
Trois façons d'y arriver, par ordre de préférence :

| Route | Déclencheur | Téléchargement | Installation | Fermeture | Quand |
|---|---|---|---|---|---|
| 1. Native pilotée | Directive DM reçue par le plugin | LibreOffice, sa pile HTTP, depuis le feed | LibreOffice, dialogue « Mise à jour des extensions » | Plugin, proprement, sur le thread principal | Feed installé et joignable, version annoncée == cible, action `update` |
| 2. Dirigée (fix/MAJ) | Directive DM | Plugin, failover multi-bootstrap, sha256 | Plugin, `addExtension` main thread | Plugin, idem | Amorçage (extension sans bloc feed), rollback, campagne expérimentale, feed injoignable ou divergent, route 1 impossible |
| 3. Manuelle | Échec de 2 | Déjà fait par 2 | Utilisateur, Gestionnaire des extensions | Utilisateur | Filet validé GPO |

Le bouton « Vérifier les mises à jour » du Gestionnaire des extensions reste utilisable
par le support, indépendamment du plugin, dès que le feed est servi.

Fait établi dans les sources LibreOffice (`desktop/source/deployment/gui/dp_gui_service.cxx`,
`extensions/source/update/check/updatecheck.cxx`) : le service créable
`com.sun.star.deployment.ui.PackageManagerDialog` implémente `XJobExecutor`, et
`trigger("SHOW_UPDATE_DIALOG")` ouvre directement le dialogue de mise à jour des
extensions. C'est le chemin qu'emprunte LibreOffice quand l'utilisateur clique sur sa
bulle de notification. La note contraire dans `docs/update-natif-libreoffice.md` est à
corriger.

## 4. Côté DM (`../device-management`, branche `feat/libreoffice-update-feed`)

### 4.1 Endpoint `GET /catalog/{slug}/update.xml`

Public, sans authentification, à côté de `catalog_updates_xml` dans `app/main.py`.
Chemin conventionnel déjà cuit par `scripts/inject_update_feed.py` côté plugin.
Ne pas confondre avec `/catalog/{slug}/updates.xml`, le manifeste Chromium.

Résolution :
1. Plugin par `slug`, `status = 'active'`, `device_type = 'libreoffice'` ; sinon 404.
2. Dernière version `plugin_versions.status = 'published'` triée par `published_at`,
   même requête que `catalog_download` ; sinon 404. Les versions expérimentales ou
   taguées ne sont jamais annoncées.
3. Identifiant d'extension : colonne `plugins.extension_id` (déjà créée par la migration
   002 pour l'identité d'auto-update, utilisée comme appid Chromium). Pour un plugin
   LibreOffice elle porte l'identifiant OXT, ici `fr.gouv.interieur.mirai`. Vide → 404
   et avertissement en log.
4. URL de téléchargement : `PUBLIC_BASE_URL` + `/catalog/{slug}/download/{slug}-{version}.oxt`,
   route versionnée existante. Si `PUBLIC_BASE_URL` est vide, `request.base_url` avec
   la réécriture http → https déjà pratiquée par la route Chromium.

Réponse, `Content-Type: text/xml; charset=utf-8`, `Cache-Control: no-cache` :

```xml
<?xml version="1.0" encoding="UTF-8"?>
<description xmlns="http://openoffice.org/extensions/update/2006"
             xmlns:xlink="http://www.w3.org/1999/xlink">
  <identifier value="fr.gouv.interieur.mirai"/>
  <version value="0.0.1.0.32"/>
  <update-download>
    <src xlink:href="https://<dm>/catalog/mirai-libreoffice/download/mirai-libreoffice-0.0.1.0.32.oxt"/>
  </update-download>
</description>
```

Attributs échappés avec `xml.sax.saxutils.quoteattr`. LibreOffice compare les versions
segment par segment, le schéma à cinq segments est géré.

### 4.2 Statut `deferred`

Dans `_update_campaign_device_status_sync`, le mappage devient :
`installed → updated`, `deferred → notified`, tout le reste → `failed`.
`notified` est déjà autorisé par la contrainte CHECK de `campaign_device_status`.
Aujourd'hui `deferred` est compté en échec, ce qui fausse `failure_rate` avec le cycle
en deux temps de fix/MAJ.

### 4.3 Tests DM

`tests/test_libreoffice_update_feed.py`, sur le patron de `test_campaign_plugin_scoping.py`
(psycopg2 factice, `TestClient`) :
- 200, XML bien formé, namespace `http://openoffice.org/extensions/update/2006`,
  identifiant, version, `src` absolu versionné ;
- 404 : slug inconnu, plugin non LibreOffice, aucune version publiée, `extension_id` vide ;
- une version expérimentale plus récente n'est pas annoncée ;
- mappage `deferred → notified` dans l'upsert de statut.

### 4.4 Documentation DM

Une section courte dans `docs/plugin-developer/` : contrat du feed, champ
`extension_id` à renseigner sur la fiche plugin, distinction avec `updates.xml`.

## 5. Côté plugin (branche `feat/maj-native-pilotee` depuis `origin/fix/MAJ`)

### 5.1 Constantes

| Nom | Valeur | Rôle |
|---|---|---|
| `_UPDATE_POSTPONE_SECONDS` | 24 h | Délai avant de reproposer une cible refusée ou ignorée |
| `_NATIVE_INSTALL_WAIT_SECONDS` | 900 | Attente maximale d'une installation après ouverture du dialogue natif |
| `_NATIVE_POLL_SECONDS` | 5 | Période de surveillance de la version installée |
| `_NATIVE_TRIGGER_TIMEOUT_SECONDS` | 30 | Attente de l'exécution du déclenchement sur le thread principal |
| `_NATIVE_MAX_ATTEMPTS` | 2 | Au-delà, la cible passe en route dirigée |
| `_PROMPT_WIZARD_WAIT_SECONDS` | 120 | Attente maximale de la fermeture de l'assistant d'enrôlement |
| `_PROMPT_GRACE_SECONDS` | 30 | Délai de grâce après l'assistant, avant de reproposer une mise à jour |
| `_CLOSE_RETRY_SECONDS` | 120 | Délai total de réessai de la fermeture après installation |
| `_CLOSE_RETRY_INTERVAL_SECONDS` | 3 | Période entre deux tentatives de fermeture |
| `_CLOSE_USER_REFUSAL_SECONDS` | 1.0 | Durée d'un veto au-delà de laquelle il est traité comme un refus humain |

### 5.2 État persistant `pending_update/update_state.json`

Champs existants : `campaign_id`, `target_version`, `version_before`, `stage`, `ts`.
Champs ajoutés : `route` (`native` ou `directed`), `postponed_until` (epoch, 0 si aucun),
`native_attempts` (entier).
Étapes existantes : `staged`, `user_accepted`, `installed_inprocess`.
Étapes ajoutées : `native_dialog`, `installed_native`, `postponed`.
`_save_update_state` reçoit `route`, `postponed_until` et `native_attempts` en
arguments nommés facultatifs et conserve les valeurs non fournies.

### 5.3 Flux

**Réception de la directive, `_schedule_update`.** Gardes existantes : anti-boucle
`_update_launch_blocked_cls`, mise à jour en cours. Garde ajoutée : si l'état persistant
porte la même `target_version` et `postponed_until` dans le futur, on saute, avec une
ligne de log. Fini le retéléchargement et le re-prompt à chaque rafraîchissement de
config. Le worker réconcilie d'abord une mise à jour précédente d'une autre cible
(rapport `installed` si elle est active), puis une cible déjà installée en attente de
redémarrage est ignorée.

**Attente commune, `_wait_before_prompting`.** Le code existant de fix/MAJ, factorisé :
attente de la fin de l'assistant d'enrôlement (120 s max) puis délai de grâce de 30 s,
puis revérifie l'assistant après la grâce.

**Choix de route, dans `_perform_update`, avant tout téléchargement.**
Si `action == "update"`, si `urgency != "deferred"` (une directive différée ne dérange
pas l'utilisateur, fix/MAJ la traite en téléchargement seul), si
`native_attempts < _NATIVE_MAX_ATTEMPTS`, et si `_native_feed_offers(target_version)` est
vrai, alors `_perform_native_update` ; si elle renvoie vrai, terminé. Sinon, flux dirigé
de fix/MAJ inchangé.

**`_native_feed_offers(target)`**, sur le thread worker. Obtient le singleton
`/singletons/com.sun.star.deployment.PackageInformationProvider` par `getValueByName`
(sans import, comme fix/MAJ), appelle `isUpdateAvailable(identifiant)`, qui interroge le
feed cuit dans l'extension installée avec la pile HTTP de LibreOffice. Vrai si une paire
`(identifiant, version)` correspond exactement à la cible. Toute exception → faux, avec
log. Ce test couvre d'un coup : bloc feed absent, feed injoignable, version divergente.
LibreOffice exécute lui-même cette API depuis un thread de fond.

**`_trigger_native_update_dialog()`**, sur le thread principal via `AsyncCallback` et le
`_MainThreadCallback` de fix/MAJ : crée `com.sun.star.deployment.ui.PackageManagerDialog`
et appelle `trigger("SHOW_UPDATE_DIALOG")`. L'appel met en file la vérification et rend la
main ; les dialogues s'exécutent sur le thread de commandes de LibreOffice. Vrai si
l'appel s'est exécuté sans exception avant le délai, faux sinon.

**`_perform_native_update(directive)`**, sur le worker :
1. `_wait_before_prompting()`.
2. Déclenchement ; faux → renvoie faux, le flux dirigé prend le relais, rien n'est
   rapporté ni persisté.
3. Rapport `deferred` au DM, état `native_dialog` avec `route = native` et
   `native_attempts + 1`, télémétrie `UpdateNativeDialogShown`.
4. Surveillance toutes les 5 s pendant 900 s au plus : version du registre
   (`getExtensionList`) ou dossier de paquet apparu dans le cache depuis l'instantané
   pris avant l'attente ; confirmée sur deux lectures consécutives. Puis état
   `installed_native`, télémétrie, fermeture retentée (veto de LibreOffice tant que sa
   fenêtre de progression est ouverte ; refus humain respecté ; abandon après 120 s →
   `UpdateCloseDeferred`, activation au prochain démarrage).
5. Délai écoulé : l'utilisateur a annulé, ignoré ou fermé. État `postponed` avec
   `postponed_until = maintenant + 24 h`, télémétrie `UpdatePostponed`. Renvoie vrai.

Après l'installation native, LibreOffice garde l'ancien paquet chargé jusqu'au
redémarrage ; le nouveau dossier apparaît à côté dans le cache des paquets. Entre la
détection et la fermeture, le worker n'importe aucun module du plugin : seuls des
appels UNO et des modules déjà chargés.

**Flux dirigé.** Inchangé, à une exception : un « Non » enregistre `postponed` avec
`postponed_until`, en plus de la télémétrie `UpdatePostponed` déjà émise.

**Réconciliation au démarrage.** Inchangée : cible active → `installed` au DM,
`ExtensionUpdated` confirmé, purge, levée de l'anti-boucle ; état périmé → purge.
Ajout : l'attribut `route` lu dans l'état est joint à `ExtensionUpdated`.

### 5.4 Télémétrie

- Nouvel événement `UpdateNativeDialogShown`, action `update` dans `_ACTION_NAMES`.
- Attribut `route` sur `UpdateAccepted`, `UpdatePostponed`, `UpdateInstalledPendingRestart`,
  `UpdateInstallFailed`, `UpdateNativeDialogShown`, `ExtensionUpdated`.
- Les événements du flux de mise à jour sont ajoutés à `_TECHNICAL_EVENTS` :
  `UpdateStaged`, `UpdateAccepted`, `UpdatePostponed`, `UpdateInstalledPendingRestart`,
  `UpdateInstallFailed`, `UpdateNativeDialogShown`, `ExtensionUpdated`, `NativeFeedCheck`.
  Sans cela, le pipeline sécurisé les jette avant la liaison utilisateur et l'entonnoir
  de campagne reste aveugle sur une partie de la flotte.
- `NativeFeedCheck` de fix/MAJ est conservé : c'est la sonde de flotte qui dit si la pile
  HTTP de LibreOffice atteint le feed à travers le proxy et le TLS des postes durcis.

### 5.5 Rapport au DM

Vocabulaire inchangé : `deferred` quand le dialogue est montré ou l'artefact stagé,
`installed` à la réconciliation seulement, `failed` en échec avéré. Aucun nouveau statut.

### 5.6 Tests plugin

Extension de `tests/unit/test_native_update.py`, sur les stubs UNO existants
(`tests/stubs/uno_stubs.py`, `AsyncCallback` synchrone) :
- choix de route : feed annonce la cible → native ; feed vide ou divergent → dirigée ;
  `rollback` → dirigée ; `native_attempts` atteint → dirigée ;
- déclenchement : service créé, `trigger("SHOW_UPDATE_DIALOG")` appelé sur le callback
  main thread ; exception → faux ; délai dépassé → faux ;
- surveillance : version devenue cible → état `installed_native` et fermeture appelée ;
  délai écoulé → `postponed` avec `postponed_until` futur et télémétrie ;
- `_schedule_update` saute une cible en cooldown et reprend après échéance ;
- « Non » en route dirigée pose le cooldown ;
- réconciliation joint `route` à `ExtensionUpdated` ;
- les événements Update* sont dans `_TECHNICAL_EVENTS`.
La règle existante `test_core_and_ui_never_pump_events` reste vérifiée.

### 5.7 Documentation plugin

- `docs/update-natif-libreoffice.md` : corriger la note sur le déclenchement
  programmatique, décrire l'ordre des routes, le cooldown, les états.
- README, section « Mise à jour automatique côté plugin » : trois routes dans l'ordre.

## 6. Amorçage et déploiement

1. Les postes en 0.0.1.0.30 n'ont pas de bloc `<update-information>`. La première release
   issue de cette branche est livrée par la route dirigée (campagne DM classique), ou à la
   main si le poste durci la refuse.
2. Avant la campagne : renseigner `extension_id = fr.gouv.interieur.mirai` sur la fiche
   plugin du DM, publier la version, vérifier `curl <bootstrap>/catalog/mirai-libreoffice/update.xml`.
3. Dès que cette release est active sur un poste, toutes les suivantes passent par la
   route native. `description.xml` versionné reste intact ; l'injection au build de
   fix/MAJ continue de porter les URL de feed, une par `bootstrap_urls` du profil.
4. Le numéro de version de la release est une décision de livraison, hors de cette spec.

## 7. Validation terrain sur poste durci, avant fusion large

Sonde de cinq lignes, sans déployer le plugin, à exécuter dans une macro Basic sur une
version qui embarque le bloc feed :

```basic
Sub TestMirAIUpdateDialog
    Dim oDlg As Object
    oDlg = createUnoService("com.sun.star.deployment.ui.PackageManagerDialog")
    oDlg.trigger("SHOW_UPDATE_DIALOG")
End Sub
```

Liste de contrôle :
1. Le dialogue apparaît, l'extension est listée et cochée, « Installer » aboutit.
2. Trois cycles consécutifs N → N+1 → N+2 → N+3 : l'extension reste visible et
   fonctionnelle, aucune entrée fantôme `addon_fr.gouv.interieur.mirai.toolbar` dans
   `registrymodifications.xcu`.
3. Télémétrie `NativeFeedCheck` positive depuis le poste (proxy et TLS de la pile LO).
4. En cas de refus : journal AppLocker (événement 8004) ou journal ASR pour nommer la règle.

## 8. Risques et limites

- `addExtension` n'a jamais été exercé sur un poste durci. Les routes 1 et 2 partagent
  cet appel. Si la politique le refuse, seul un déploiement par l'outillage du ministère
  reste possible. La validation du §7 lève ce point avant tout déploiement large.
- La route native ne vérifie pas de sha256 ; la confiance repose sur TLS vers le DM.
  La route dirigée conserve le sha256.
- Le feed est anonyme : tout poste qui vérifie voit la version publiée. Le canary tient
  au déclenchement, pas au contenu. Accepté.
- « Ignorer cette version » dans le dialogue natif n'est pas visible du plugin ;
  `_NATIVE_MAX_ATTEMPTS` borne les tentatives avant bascule en route dirigée.
- Le réglage GPO `DisableExtensionInstallation`, s'il était poussé, grise « Ajouter » et le
  glisser-déposer, pas le dialogue de mise à jour ni l'API : seul le repli manuel serait
  atteint.
- LibreOffice sans le module de mise à jour d'extensions (paquets Linux de distribution)
  masque le bouton natif ; les builds Windows et macOS de la Document Foundation l'ont.
- Mesuré sur le banc du 2026-09-22 : le registre reste ancien en session, `terminate()`
  est refusé tant que la fenêtre de progression est ouverte, et LibreOffice ne propose
  pas de redémarrer sur le chemin programmatique (l'invite n'existe qu'à la fermeture du
  Gestionnaire des extensions).

## 9. Hors périmètre

Charge utile Python chargée depuis le profil, scripts et entrées RunOnce ou LaunchAgent,
déploiement SCCM ou Intune, installation à la fermeture de LibreOffice, remplissage
automatique de `extension_id` à l'upload d'un OXT, affichage de l'URL du feed dans
l'admin DM. Chacun ferait l'objet d'une décision séparée.

## 10. Références

- LibreOffice : `desktop/source/deployment/gui/dp_gui_service.cxx` (`trigger`,
  `startExecuteModal`), `dp_gui_theextmgr.cxx` (`checkUpdates`),
  `dp_gui_extensioncmdqueue.cxx` (`_checkForUpdates`), `dp_gui_updateinstalldialog.cxx`
  (`addExtension`, `VersionException` approuvée), `dp_gui_dialog2.cxx` (réglages
  `DisableExtension*`), `desktop/source/deployment/manager/dp_informationprovider.cxx`
  (`isUpdateAvailable`), `desktop/source/deployment/registry/package/dp_package.cxx`
  (`checkLicense`, `suppress-on-update`), `extensions/source/update/check/updatecheck.cxx`
  et `org/openoffice/Office/Jobs.xcu` (contrôle hebdomadaire).
- Plugin : `docs/update-natif-libreoffice.md`, `scripts/inject_update_feed.py`,
  `tests/unit/test_native_update.py`, `tests/simulation/deploy_simulator.py` (fix/MAJ).
- DM : `app/main.py` (`catalog_download`, `catalog_updates_xml`,
  `_update_campaign_device_status_sync`, `_build_update_directive`), `db/schema.sql`,
  `alembic/versions/002_extension_update_metadata.py`.
- Thunderbird, pour l'analogie : `iassistant/thunderbird/60.9.1/modules/auto-updater.js`.
