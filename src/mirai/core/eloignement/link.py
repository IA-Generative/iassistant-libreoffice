"""Le lien d'un document Writer avec son dossier Éloignement.

Il vit dans les propriétés personnalisées du document
(`DocumentProperties.UserDefinedProperties`), jamais dans son texte : une
copie enregistrée sur le poste puis rouverte retrouve son dossier, et rien
n'apparaît à l'impression.

À appeler sur le thread principal (accès UNO).
"""

import dataclasses

PROPERTIES = {
    "dossier": "MirAI.Eloignement.Dossier",
    "modele": "MirAI.Eloignement.Modele",
    "document": "MirAI.Eloignement.Document",
    "version": "MirAI.Eloignement.Version",
}

# com.sun.star.beans.PropertyAttribute.REMOVEABLE, sans import UNO.
_REMOVEABLE = 128


@dataclasses.dataclass
class Link:
    dossier: str
    modele: str = ""
    document: str = ""
    version: str = ""


def _user_properties(model):
    return model.getDocumentProperties().getUserDefinedProperties()


def _has(props, name):
    try:
        return bool(props.getPropertySetInfo().hasPropertyByName(name))
    except Exception:
        return False


def read_link(model):
    """Le lien du document, ou None s'il n'est lié à aucun dossier."""
    try:
        props = _user_properties(model)
    except Exception:
        return None
    values = {}
    for field, name in PROPERTIES.items():
        value = ""
        if _has(props, name):
            try:
                value = str(props.getPropertyValue(name) or "").strip()
            except Exception:
                value = ""
        values[field] = value
    if not values["dossier"]:
        return None
    return Link(**values)


def write_link(model, link):
    """Écrit les quatre propriétés (en texte). Le document devient modifié."""
    props = _user_properties(model)
    for field, name in PROPERTIES.items():
        value = str(getattr(link, field) or "")
        if _has(props, name):
            props.setPropertyValue(name, value)
        else:
            props.addProperty(name, _REMOVEABLE, value)


def set_version(model, version):
    props = _user_properties(model)
    name = PROPERTIES["version"]
    if _has(props, name):
        props.setPropertyValue(name, str(version or ""))
    else:
        props.addProperty(name, _REMOVEABLE, str(version or ""))


def clear_link(model):
    props = _user_properties(model)
    for name in PROPERTIES.values():
        if _has(props, name):
            try:
                props.removeProperty(name)
            except Exception:
                pass
