# Paquet neutre, pré-versions et signature

Ce que la CI publie, sous quel numéro, et comment lancer un essai. Le format de
signature est celui de l'ADR-0006 du Device Management (DM).

## Un seul paquet, neutre

La CI ne lit aucune valeur d'organisation ni aucun secret de configuration.
Chaque version donne **un** fichier, le même pour toutes les instances :

    mirai-libreoffice-<version>.oxt

Le slug `mirai-libreoffice` est le nom du plugin au catalogue du DM. Il n'est
défini qu'à un endroit : le champ `slug` de `dm-manifest.json`, que le workflow
`build-package.yml` lit pour nommer le fichier et que la signature reprend.

Un paquet neutre porte :

- la liste de DM d'exemple `https://dm.example.invalid`
  (`config/profiles/config.default.neutral.json`), et le flux
  `<update-information>` de `description.xml` qui en dérive ;
- un chemin de configuration sans `?profile=` : le DM applique son profil par
  défaut (`DM_CONFIG_PROFILE`) ;
- un `dm-config.json` où toute adresse réelle est devenue un repère que le DM
  résout : `portal_url` → `${{PORTAL_URL}}`, `doc_url` → `${{DOC_URL}}`. Les
  repères déjà présents (`${{LLM_BASE_URL}}`…) et les adresses locales du profil
  `local` sont gardés.

Chaque DM adapte le paquet à l'import : il y inscrit sa liste de DM (dans
`config.default.json` et dans le flux de mise à jour) et sert sa configuration.
Un DM qui ne définit pas `PORTAL_URL` et `DOC_URL` sert ces deux liens vides :
les entrées « Portail » et « Documentation » du menu ne s'ouvrent plus.

Restent dans le paquet, parce qu'ils décrivent l'éditeur du plugin et non une
instance : l'identifiant `fr.gouv.interieur.mirai`, l'éditeur de
`description.xml`, la fiche `dm-manifest.json` (dont l'adresse de support) et
la chaîne de certification publique de `src/mirai/CAbundle/`.

`02-build-oxt.sh --neutral` refuse de produire un paquet qui nommerait un vrai
DM (`scripts/neutral_package.py check`), y compris par `MIRAI_UPDATE_FEED_URL`.

### Ne pas installer un paquet neutre tel quel

L'adresse d'exemple n'est pas un DM : `.invalid` est un domaine réservé qui ne
répond jamais. Le plugin l'écarte là où il lit sa liste
(`local_config.dm_urls`, adresse nommée par `local_config.EXAMPLE_DM_URL`),
comme toute adresse en `.invalid`. Installé sans passer par un DM :

- le plugin n'interroge aucun DM : ni configuration servie, ni enrôlement, ni
  mise à jour par directive, ni télémétrie. Seuls les réglages saisis à la main
  sur le poste s'appliquent ;
- installé par-dessus un paquet adapté, il change l'environnement visé : les
  identifiants de l'ancien DM sont effacés, comme pour tout changement de liste
  de DM, et le poste se ré-enrôle quand il reçoit de nouveau un paquet adapté ;
- le flux natif de LibreOffice (« Vérifier les mises à jour ») vise l'adresse
  d'exemple et ne trouve rien.

Un paquet neutre passe donc toujours par un DM.

### Build local réel

Rien ne change pour un développeur : sans `--neutral`, `02-build-oxt.sh
--config <profil>` embarque le profil demandé et le `dm-config.json` du dépôt.
Le build neutre se reproduit avec :

```bash
./scripts/02-build-oxt.sh --neutral --version 0.4.0-rc.12 \
  --output dist/mirai-libreoffice-0.4.0-rc.12.oxt
```

## Branches et numéros

| Origine | Déclencheur | Numéro | Publication |
|---|---|---|---|
| `master` | fusion de la PR release-please | `X.Y.Z` | release |
| `develop` | chaque push | `X.Y.Z-rc.N` | pré-version |
| `exp/**` | lancement manuel du workflow | `X.Y.Z-dev.N` | pré-version |

- `X.Y.Z` est la **prochaine version de master** : celle de la PR release-please
  ouverte ; sans PR ouverte, la version du manifeste release-please de master,
  mineure + 1 (`scripts/prerelease-version.sh`).
- `N` est le numéro du run du workflow `Pre-release` : il ne fait que croître.
- Une candidate précède sa version finale, un essai précède une candidate :
  `0.4.0-dev.7` < `0.4.0-rc.12` < `0.4.0`.
- Le numéro n'est **pas committé**. Il est inscrit dans le paquet au build
  (`description.xml`, `dm-manifest.json`, texte de licence), et le tag
  `v<version>` est posé sur le commit construit.

Limite connue : si la prochaine version de master recule (par exemple `0.4.0`
calculée sans PR, puis une PR release-please qui annonce `0.3.1`), les
candidates suivantes sont plus petites que les précédentes, et un poste ne
recule pas.

### Le numéro dans `description.xml`

LibreOffice ne refuse pas un suffixe dans `<version value="…">`, mais il le
range mal. Il compare les versions élément par élément, entre les points :
zéros de tête retirés, le plus long l'emporte, puis l'ordre des caractères
(`dp_misc::compareVersions`, `desktop/source/deployment/misc/dp_version.cxx`).
`0.4.0-rc.12` se découpe en « », « 4 », « -rc », « 12 » ; face à `0.4.0`, le
troisième élément « -rc » est plus long que l'élément vide : la candidate est
**plus récente que sa version finale**, et que `0.4.1`. Un poste en candidate
ne se verrait jamais proposer la version finale par le flux natif.

Le numéro du DM et du nom de fichier reste donc `X.Y.Z-rc.N`, et
`description.xml` en reçoit une écriture numérique qui se range correctement
(`src/mirai/package_version.py`, appelé par le build) :

| Numéro (DM, fichier, notes) | `<version value>` de `description.xml` |
|---|---|
| `X.Y.Z` | `X.Y.Z` |
| `X.Y.Z-dev.N` | `P.1.N` |
| `X.Y.Z-rc.N` | `P.2.N` |

`P` est le plus grand numéro à trois éléments placé avant `X.Y.Z` :
`X.Y.(Z-1)` ; si `Z` vaut 0, `X.(Y-1).99999` ; si `Y` vaut 0 aussi,
`(X-1).99999.99999`.

| Numéro | `description.xml` |
|---|---|
| `0.4.0-rc.12` | `0.3.99999.2.12` |
| `0.4.0-dev.7` | `0.3.99999.1.7` |
| `0.3.1-rc.5` | `0.3.0.2.5` |
| `1.0.0-rc.3` | `0.99999.99999.2.3` |

Ainsi `0.3.0` < `0.3.1` < `0.4.0-dev.7` < `0.4.0-rc.12` < `0.4.0` pour
LibreOffice aussi (`tests/unit/test_package_version.py` le vérifie avec une
transcription de sa comparaison). La correspondance s'inverse : le plugin relit
dans le registre de LibreOffice l'écriture numérique et **annonce au DM le
numéro que le DM connaît** (`X-Plugin-Version`, statuts de mise à jour,
télémétrie). Le Gestionnaire des extensions, lui, affiche l'écriture numérique.

Deux contraintes en découlent : aucun élément d'un numéro n'atteint 99999, et
une version à cinq éléments dont le quatrième vaut 1 ou 2 est toujours lue
comme une pré-version (les anciens numéros `0.0.1.0.NN` ne sont pas concernés).

**Ce que le DM doit accepter.** Pour un paquet `.oxt` enregistré sous
`X.Y.Z-rc.N` ou `X.Y.Z-dev.N` :

1. à l'import, `description.xml` annonce l'écriture numérique ci-dessus, pas le
   numéro enregistré : le contrôle de concordance doit l'accepter (égalité avec
   le numéro, ou avec son écriture numérique) ;
2. le flux `update.xml` doit annoncer dans `<version value>` l'écriture
   numérique, sans quoi LibreOffice compare un suffixe ; `?version=` et le nom
   du fichier gardent le numéro du DM ;
3. rien d'autre : la directive (`target_version`), les en-têtes et les statuts
   échangés avec le plugin portent le numéro du DM.

Tant que le DM n'annonce pas l'écriture numérique dans son flux, le plugin
n'emprunte pas la route native pour une pré-version (le flux n'annonce pas la
cible attendue) : il passe par la route dirigée. Mais le bouton « Vérifier les
mises à jour » de LibreOffice, lui, lirait dans ce flux un numéro à suffixe
qu'il croit plus récent que la pré-version installée, et la reproposerait sans
fin : le point 2 est à livrer avant de diffuser une pré-version à des postes.

## Lancer un essai

1. Créer la branche depuis `develop` : `git switch -c exp/mon-essai origin/develop`,
   pousser.
2. Lancer le workflow depuis cette branche :

   ```bash
   gh workflow run prerelease.yml --ref exp/mon-essai
   ```

   (ou onglet Actions, « Pre-release », « Run workflow », choisir la branche).
   Lancé depuis une autre branche, le workflow échoue sans rien publier.
3. La pré-version `vX.Y.Z-dev.N` apparaît dans les releases avec son paquet.
   L'importer dans le DM d'essai.

GitHub n'accepte un lancement manuel que si `prerelease.yml` existe sur la
branche par défaut (`master`) : les essais deviennent possibles une fois ce
workflow arrivé sur `master`.

## Signature

Le paquet est accompagné de `<fichier>.sig.json` : une enveloppe
`dm-package-signature/1` dont la déclaration (`kind: "build"`) porte le slug, la
version, le nom, l'empreinte SHA-256 et la taille du fichier, et son origine
(`source` : dépôt, référence, commit, workflow, run). Algorithme ES256,
signature brute `r||s`. La version signée est le numéro du DM (`X.Y.Z-rc.N`).

- La clé privée (EC P-256, PEM, sans mot de passe) est le secret
  `PACKAGE_SIGNING_KEY` de l'environnement GitHub `package-signing`. Les règles
  de l'environnement décident des branches qui peuvent signer.
- **Sans ce secret**, le workflow avertit et publie le paquet sans enveloppe.
- Le DM vérifie l'enveloppe à l'import avec la clé publique
  (`DM_PACKAGE_PUBLISHER_KEYS`).

Créer la paire (hors du dépôt) et obtenir le `keyid` :

```bash
openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out signature.pem
openssl pkey -in signature.pem -pubout -out editeur.pub.pem
openssl pkey -pubin -in editeur.pub.pem -outform DER | shasum -a 256 | cut -c1-16
```

Vérifier un paquet téléchargé :

```bash
pip install -r scripts/requirements-signing.txt
scripts/package_signature.py verify mirai-libreoffice-0.4.0-rc.12.oxt \
  --public-key editeur.pub.pem --slug mirai-libreoffice --version 0.4.0-rc.12
```

## Conservation

- **`rc`** : gardées jusqu'à la sortie de la version finale `X.Y.Z`, puis supprimées.
- **`dev`** : supprimées à la fin de l'essai.

```bash
gh release delete v0.4.0-dev.31 --cleanup-tag --yes
```

`--cleanup-tag` retire aussi le tag. Lister ce qui reste :

```bash
gh release list --limit 100 | grep -E -- '-(rc|dev)\.'
```
