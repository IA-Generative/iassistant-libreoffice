"""Langue de l'interface LibreOffice, lue par la coquille.

entrypoint importe ce module à son chargement, sur le thread principal : un
import UNO paresseux échouerait hors de ce thread.
"""

from com.sun.star.beans import PropertyValue

from ..i18n import resolve_locale


def ui_locale(ctx):
    """Langue de LibreOffice (/org.openoffice.Setup/L10N) si l'extension la
    propose, sinon anglais."""
    try:
        provider = ctx.getServiceManager().createInstanceWithContext(
            "com.sun.star.configuration.ConfigurationProvider", ctx)
        node = PropertyValue()
        node.Name = "nodepath"
        node.Value = "/org.openoffice.Setup/L10N"
        access = provider.createInstanceWithArguments(
            "com.sun.star.configuration.ConfigurationAccess", (node,))
        return resolve_locale(getattr(access, "ooLocale", None))
    except Exception:
        return resolve_locale(None)
