"""Libellé de la version affiché par le dialogue « À propos »."""

from .. import package_version
from ..i18n import t as _t


def version_label(version):
    """« Version 0.4.0 », suivi de la nature d'une pré-version : « Version
    candidate » (X.Y.Z-rc.N) ou « Version d'essai » (X.Y.Z-dev.N)."""
    label = _t("about.version", version=version)
    kind = package_version.prerelease_kind(version)
    if kind == package_version.RELEASE_CANDIDATE:
        return f"{label} — {_t('about.prerelease_rc')}"
    if kind == package_version.TRIAL:
        return f"{label} — {_t('about.prerelease_dev')}"
    return label
