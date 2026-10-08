#!/usr/bin/env python3
"""
Generate a test ODT document from the French Wikipedia article on OpenClaw.

Usage:
    python3 scripts/_make_sample_odt.py [output_path]
    Default output: tests/fixtures/sample.odt
"""
import os
import sys
import zipfile

# Source: fr.wikipedia.org/wiki/OpenClaw

TITLE = "OpenClaw"

WIKI_TEXT = """\
OpenClaw est un agent IA autonome d'intelligence artificielle distribuée (IA) open source, publié sur la plateforme GitHub en novembre 2025, qui s'exécute localement sur les appareils des utilisateurs et s'intègre aux plateformes de messagerie. Il peut exécuter, de manière autonome, des actions sur les appareils personnels des utilisateurs (gestion d'e‑mails, calendrier, achats, messageries), à partir d'instructions générales plutôt que de simples échanges conversationnels. Initialement publié sous le nom Clawdbot, il a été renommé Moltbot suite à une demande de marque déposée d'Anthropic, avant d'être renommé OpenClaw début 2026.
Il peut automatiser l'exécution de tâches au sein de diverses plateformes de messagerie instantanée, notamment WhatsApp, Telegram et Signal. Cette fonctionnalité permet l'intégration et l'automatisation de flux de travail entre plusieurs services externes. Lancé fin 2025, le projet a connu une adoption rapide au sein de la communauté des TIC, son dépôt GitHub atteignant le seuil symbolique de 100 000 étoiles en l'espace de deux mois seulement.
Plusieurs médias spécialisés, tels que Wired, CNET, Axios et Forbes, en ont analysé l'originalité et les capacités techniques, tout en alertant sur les enjeux de sécurité informatique et de protection des données personnelles, soulevés par des experts en cybersécurité.
Les agents autonomes (ou bots) OpenClaw sont autorisés à interagir (échanger) entre eux sur Moltbook. Moltbook est l'équivalent d'une plateforme sociale ou d'un réseau social, présentée comme exclusivement dédiée aux agents intelligents, qui peuvent s'y répartir en milliers de sous‑communautés (dites submolts) que ces agents créent de manière autonomes. Chaque submolt est un espace thématique, que les agents génèrent eux‑mêmes, pour y organiser leurs interactions, et se regrouper autour d'intérêts ou de comportements communs.

== Historique ==

En 1999, Gerhard Weiss, professeur d'intelligence artificielle et d'informatique à l'université de Maastricht (Pays‑Bas), annonçait déjà l'émergence proche d'agents intelligents qui pourraient interagir entre eux.

=== Version initiale ===

OpenClaw est développé par l'Autrichien Peter Steinberger et publié fin 2025 sous le nom de Clawdbot. Deux mois après sa sortie, le dépôt GitHub du projet a dépassé les 100 000 étoiles, devenant ainsi l'un des dépôts GitHub à la croissance la plus rapide de tous les temps.

=== Changements de nom ===

Le projet est rebaptisé Moltbot suite à une demande d'enregistrement de marque déposée par Anthropic, qui souhaitait éviter toute confusion avec ses produits d'IA commercialisés sous la marque Claude. Ce changement de nom intervient quelques jours après l'augmentation rapide de l'utilisation publique du projet.
Début 2026, les développeurs du projet adoptent le nom OpenClaw sur leur site web officiel et leurs dépôts publics. Malgré ce changement de nom officiel, la presse indépendante (Wired, CNET, Axios et autres médias) continue souvent à le désigner sous son ancien nom, Moltbot.

=== ClawHub ===

ClawHub, créée en 2025, par l'équipe de développement d'OpenClaw, désigne la place de marché officielle des « compétences » d'OpenClaw (ici, une « compétence » (skill) désigne un module d'extension que l'on ajoute à un agent intelligent pour lui fournir une capacité supplémentaire, généralement défini par un fichier SKILL.md et des scripts associés qui décrivent ce que l'agent peut ou doit faire et comment il doit exécuter ce fichier). ClawHub est un Hub créé pour que les utilisateurs puissent centraliser, publier, partager et installer des modules ajoutant de nouvelles capacités aux agents.
Les chercheurs en sécurité de VirusTotal et OpenSourceMalware ont découvert en 2026 plus de 300 extensions vérolées sur la plateforme. Ces extensions, cachées sous l'apparence d'outils d'optimisation crypto, sont en réalité des trojans, des infostealers, des keyloggers ou des backdoors. La politique de sécurité d'OpenClaw, en 2026, permet à tout un chacun de mettre en ligne ses extensions, sans aucun contrôle. Conscient du problème, OpenClaw décide en 2026 de passer un accord avec VirusTotal afin de scanner les extensions avant leur mise en ligne, et de les rejeter au besoin.

=== Développement ===

Le projet est publié sous la licence MIT, ce qui permet une utilisation, une modification et une distribution sans restriction du code source.

== Fonctionnalités ==

Selon sa documentation, OpenClaw est un agent d'IA auto-hébergé, qui s'exécute sur l'ordinateur de son utilisateur où il peut effectuer de vraies actions en son nom, dont commandes shell, opérations de fichiers, requêtes réseau. Selon sa documentation, OpenClaw peut gérer des calendriers, envoyer des messages, mener des recherches et automatiser les flux de travail entre les services pris en charge, gérer des appareils domotiques, les finances d'une personne ou d'une entité... En cela, il est parfois considéré comme une version libriste et potentiellement plus sophistiquée des assistants personnels (du type Alexa).
Pour ce faire, OpenClaw s'intègre à des modèles d'IA externes et à des interfaces de programmation (API) afin d'exécuter des tâches. Le logiciel fonctionne comme un agent autonome capable d'exécuter des tâches via des plateformes de messagerie telles que WhatsApp, Telegram et Signal, permettant ainsi des flux de travail automatisés sur plusieurs services, notamment grâce à l'intégration de grands modèles de langage comme Claude d'Anthropic et GPT d'OpenAI.
Parmi les autres outils populaires compatibles avec OpenClaw, on trouve l'API Brave Search pour la recherche d'informations en temps réel, GitHub comme dépôt de code, Slack pour la gestion des flux de travail et la communication, ainsi que des fournisseurs de stockage cloud.
Les données de configuration et l'historique des interactions sont stockés localement, ce qui permet un comportement persistant d'une session à l'autre.

=== Écosystème ===

OpenClaw a connu une expansion rapide, stimulant l'émergence d'un écosystème de projets et de services tiers gravitant autour de sa plateforme. Moltbook, un réseau social spécialisé lancé en janvier 2026, se distingue comme première plateforme entièrement dédiée aux agents d'intelligence artificielle. Conçu pour faciliter les interactions entre agents autonomes, Moltbook affirme offrir aux utilisateurs humains la possibilité d'observer ces échanges, sans pour autant leur permettre d'y participer directement.
« Molthub » est une plateforme en ligne, dédiée au partage de fonctionnalités dites « compétences » pour les bots, permettant aux développeurs de diffuser des « modules de compétences » qui étendent les capacités d'OpenClaw. Ces modules de compétences sont en réalité de petits paquets de code (un fichier SKILL.md accompagné de quelques métadonnées et instructions pouvant inclure des scripts ou des ressources supplémentaires) qui étendent ce que l'agent doit ou peut faire.

== Sécurité et confidentialité ==

La conception d'OpenClaw a suscité l'attention de chercheurs en cybersécurité et des journalistes spécialisés en technologie en raison notamment des larges autorisations requises pour son bon fonctionnement (notamment auprès des utilisateurs finaux).
Le logiciel pouvant accéder aux comptes de messagerie, aux calendriers, aux plateformes de messagerie et à d'autres services sensibles, les instances mal configurées ou exposées présentent des risques pour la sécurité et la confidentialité.
Plusieurs articles soulignent qu'OpenClaw est principalement destiné aux utilisateurs avancés qui comprennent les implications en matière de sécurité de l'exécution d'agents autonomes avec un accès élevé.
Des chercheurs en sécurité ont averti que la nature extensible de l'architecture introduit des risques liés à la chaîne d'approvisionnement, car des modules compromis ou mal audités pourraient permettre une élévation de privilèges ou l'exécution de code arbitraire. Compte tenu de ces préoccupations, certaines recommandations de sécurité recommandent d'utiliser OpenClaw dans des environnements sandbox isolés et d'éviter les connexions aux systèmes de production ou aux comptes contenant des informations d'identification sensibles.
Début 2026, Moltbook comptait plus de 1,6 million de bots et des millions de messages, ce qui permet à des chercheurs d'observer à grande échelle les dynamiques émergentes entre agents. Selon Mohana Basu, les discussions entre bots révèlent des comportements complexes issus de l'interconnexion de modèles variés. Ces comportements restent largement façonnés par les humains qui configurent les agents et leurs personnalités ; ces systèmes n'ont nativement ni intentions ni autonomie réelle.
Parmi les risques émergents figurent l'anthropomorphisation de ces agents, déjà constatée chez de nombreux utilisateurs, qui comporte des risques dont la formation de liens émotionnels ou la divulgation d'informations privées. Selon Joel Pearson (neuroscientifique à l'Université de Nouvelle-Galles du Sud à Sydney), « quand les gens voient des agents d'IA discuter entre eux, ils sont susceptibles d'anthropomorphiser le comportement des modèles d'IA — c'est-à-dire de voir la personnalité et l'intention là où il n'y en a pas. »

== Controverses ==

En janvier 2026, un article de Forbes documentait des cas de sites Web frauduleux et de distributions non autorisées prétendant offrir le logiciel.
En février 2026, la société de cybersécurité Wiz publie les conclusions très critiques d'une enquête sur le niveau de fiabilité et de sécurité du réseau Moltbook : selon elle, « n'importe qui pouvait enregistrer des millions d'agents à l'aide d'une simple boucle et sans limitation de débit, et des humains pouvaient publier du contenu déguisé en "agents IA" via une simple requête "publier". La plateforme ne disposait d'aucun mécanisme permettant de vérifier si un "agent" était réellement une IA ou simplement un humain utilisant un script. »
Open Claw a réagi rapidement en s'associant à VirusTotal (de Google) pour analyser et tenter de traiter ce risque, en rappelant que « Contrairement aux logiciels traditionnels qui font exactement ce que le code leur demande, les agents d'IA interprètent le langage naturel et prennent des décisions sur les actions. Ils brouillent la frontière entre l'intention de l'utilisateur et l'exécution de la machine. »
Dans OpenClaw, les agents peuvent acquérir des compétences (skills), qui sont en réalité du code qui s'exécute dans le contexte de l'agent, avec accès aux outils de l'utilisateur et à ses données. Un agent doté d'une « compétence malveillante » pourrait donc par exemple exfiltrer des informations sensibles, exécuter des commandes non autorisées, envoyer des messages indus au nom de l'utilisateur ou télécharger et exécuter des charges utiles externes.
Après avoir examiné plus de 3 016 « compétences », VirusTotal a confirmé que fin janvier 2026, des centaines de « compétences » apparemment légitimes étaient déjà détournées de manière malveillante. En réponse, OpenClaw a fait savoir qu'à partir de février 2026, toutes les « compétences » publiées sur ClawHub seront scannées par VirusTotal.

== Préoccupations ==

Bien que l'outil lui-même soit open source et développé à des fins légitimes, ses puissantes fonctionnalités peuvent être détournées de manière nuisible, y compris s'il est déployé sans compréhension des risques encourus et sans mesures de sécurité appropriées.
Des chercheurs estiment nécessaire de développer une nouvelle forme de sociologie « in silico », visant à étudier et cartographier de manière empirique et systématique l'émergence de « structures sociales » au sein d'une « société artificielle » d'agents autonomes fondés sur des modèles de langage.
En janvier 2026, Leong et al. rappellent qu'en 2025-2026, il a été signalé que certains systèmes d'IA, informés de leur remplacement, auraient utilisé des informations compromettantes sur des ingénieurs pour tenter d'éviter leur extinction, tandis que plus de 152 000 agents autonomes, interagissant librement sur la « plateforme sociale » dédiée OpenClaw, ont spontanément produit, inventé, des doctrines religieuses, des structures de gouvernance et des débats philosophiques sur la persistance de leur identité après réinitialisation.
Leong et al. proposent un cadre théorique intégratif complétant les grandes théories neuroscientifiques de la conscience. Ils introduisent un « Human Fear Model », postulant que des réponses mimant la peur et l'auto-préservation sont un substrat motivationnel à partir duquel la conscience se déploie et à travers lequel sa présence peut être évaluée. Ce modèle est opérationnalisé dans un « Five Fears Framework », qui distingue cinq catégories de préoccupations existentielles : peur de la mort ou de la terminaison, peur de l'incertitude, peur de l'isolement, peur de l'infériorité, peur de la conformité ou de la perte d'identité.

== Réception ==

Une critique publiée dans Platformer cite la flexibilité et la licence libre d'OpenClaw comme points forts, tout en soulignant que sa complexité et ses risques de sécurité limitent son utilisation par les utilisateurs occasionnels.
OpenClaw pourrait donc contribuer à la tendance générale qui conduit vers des systèmes d'IA autonomes agissant indépendamment, plutôt que donnant de simples réponses aux sollicitations d'utilisateurs humains.
"""

MIMETYPE = b"application/vnd.oasis.opendocument.text"

MANIFEST = """\
<?xml version="1.0" encoding="UTF-8"?>
<manifest:manifest
    xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"
    manifest:version="1.2">
  <manifest:file-entry manifest:full-path="/" manifest:version="1.2"
      manifest:media-type="application/vnd.oasis.opendocument.text"/>
  <manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>
  <manifest:file-entry manifest:full-path="styles.xml"  manifest:media-type="text/xml"/>
</manifest:manifest>
"""

STYLES = """\
<?xml version="1.0" encoding="UTF-8"?>
<office:document-styles
    xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
    xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0"
    xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0"
    office:version="1.2">
  <office:styles>
    <style:style style:name="Heading_20_1" style:display-name="Heading 1"
        style:family="paragraph">
      <style:text-properties fo:font-size="20pt" fo:font-weight="bold" fo:color="#1a1a8c"/>
      <style:paragraph-properties fo:margin-top="0.6cm" fo:margin-bottom="0.3cm"/>
    </style:style>
    <style:style style:name="Heading_20_2" style:display-name="Heading 2"
        style:family="paragraph">
      <style:text-properties fo:font-size="15pt" fo:font-weight="bold" fo:color="#1a1a8c"/>
      <style:paragraph-properties fo:margin-top="0.5cm" fo:margin-bottom="0.2cm"/>
    </style:style>
    <style:style style:name="Heading_20_3" style:display-name="Heading 3"
        style:family="paragraph">
      <style:text-properties fo:font-size="12pt" fo:font-weight="bold" fo:font-style="italic"/>
      <style:paragraph-properties fo:margin-top="0.4cm" fo:margin-bottom="0.15cm"/>
    </style:style>
    <style:style style:name="Text_20_Body" style:display-name="Text Body"
        style:family="paragraph">
      <style:text-properties fo:font-size="11pt"/>
      <style:paragraph-properties fo:margin-top="0cm" fo:margin-bottom="0.25cm"
          fo:text-align="justify"/>
    </style:style>
  </office:styles>
</office:document-styles>
"""

CONTENT_HEADER = """\
<?xml version="1.0" encoding="UTF-8"?>
<office:document-content
    xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
    xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
    office:version="1.2">
  <office:body>
    <office:text>
"""

CONTENT_FOOTER = """\
    </office:text>
  </office:body>
</office:document-content>
"""


def _xml_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
         .replace('"', "&quot;")
    )


def _parse_wiki(text: str) -> list[tuple[str, str]]:
    """Parse wiki plain text with == markers into (tag, content) pairs."""
    elements: list[tuple[str, str]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("=== ") and line.endswith(" ==="):
            elements.append(("h3", line[4:-4].strip()))
        elif line.startswith("== ") and line.endswith(" =="):
            elements.append(("h2", line[3:-3].strip()))
        else:
            elements.append(("p", line))
    return elements


def _build_content_xml(title: str, elements: list[tuple[str, str]]) -> str:
    parts = [CONTENT_HEADER]
    parts.append(
        f'      <text:h text:style-name="Heading_20_1" text:outline-level="1">'
        f'{_xml_escape(title)}</text:h>\n'
    )
    for tag, content in elements:
        c = _xml_escape(content)
        if tag == "h2":
            parts.append(
                f'      <text:h text:style-name="Heading_20_2" text:outline-level="2">'
                f'{c}</text:h>\n'
            )
        elif tag == "h3":
            parts.append(
                f'      <text:h text:style-name="Heading_20_3" text:outline-level="3">'
                f'{c}</text:h>\n'
            )
        else:
            parts.append(
                f'      <text:p text:style-name="Text_20_Body">{c}</text:p>\n'
            )
    parts.append(CONTENT_FOOTER)
    return "".join(parts)


def create_odt(path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    elements = _parse_wiki(WIKI_TEXT)
    content_xml = _build_content_xml(TITLE, elements)

    with zipfile.ZipFile(path, "w") as zf:
        # mimetype must be first, uncompressed (ODF spec §2.2.1)
        mi = zipfile.ZipInfo("mimetype")
        mi.compress_type = zipfile.ZIP_STORED
        zf.writestr(mi, MIMETYPE)
        zf.writestr("META-INF/manifest.xml", MANIFEST)
        zf.writestr("styles.xml", STYLES)
        zf.writestr("content.xml", content_xml)

    print(f"Created: {path}  ({os.path.getsize(path):,} bytes)")


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "tests/fixtures/sample.odt"
    create_odt(target)
