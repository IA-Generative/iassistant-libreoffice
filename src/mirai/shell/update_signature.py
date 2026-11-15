"""Signature exigée avant d'installer une mise à jour reçue par directive.

Règle de transition (ADR-0006 du Device Management) : un paquet installé qui
porte `dm-trust.json` n'installe une mise à jour que si la directive apporte
une enveloppe valide (`update.signature`) pour les octets téléchargés. Sans ce
fichier (paquet antérieur à la signature), le contrôle reste l'empreinte seule.
L'exigence vient du paquet installé : un DM ne peut pas la lever à distance.
"""

import logging

from .. import local_config, package_signature


def required(job):
    """Vrai si le paquet installé porte une ancre de confiance."""
    return package_signature.read_trust(job._package_root_dir()) is not None


def refuses(job, directive, data, extension_id, version_before):
    """Vrai si `data` ne doit pas être installé : l'ancre du paquet installé
    exige une signature, et celle de la directive ne tient pas. Le refus est
    alors journalisé, rapporté au DM (`/update/status`, `failed`,
    `signature: <raison>`) et en télémétrie, et la cible n'est plus retentée
    d'ici le redémarrage de LibreOffice."""
    trust = package_signature.read_trust(job._package_root_dir())
    if trust is None:
        return False
    target = str(directive.get("target_version") or "").strip()
    slug = local_config.dm_slug(job._get_config_from_file("config_path", ""))
    verdict = package_signature.verify_package_signature(
        directive.get("signature"), data, target, extension_id, slug, trust)
    if verdict.ok:
        logging.info("update %s: signature valid (signer %s)", target, verdict.keyid)
        return False
    logging.info("update %s refused: signature %s (%s)", target, verdict.reason, verdict.detail)
    campaign_id = directive.get("campaign_id")
    if target:
        job.runtime.update_launch_blocked.add(target)
    job._report_update_status(campaign_id, "failed", version_before, "",
                              f"signature: {verdict.reason}")
    job._send_telemetry("UpdateInstallFailed", {
        "version_after": target,
        "campaign_id": str(campaign_id) if campaign_id is not None else "",
        "route": "directed",
        "signature_reason": verdict.reason,
    })
    return True
