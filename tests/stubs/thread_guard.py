"""Garde de thread pour les faux objets UNO.

LibreOffice plante quand UNO est touché hors du thread principal. Un faux
document, ou un faux ServiceManager, enveloppé dans ThreadGuard note chaque
appel ou lecture de valeur venu d'un autre thread, y compris à travers les
objets qu'il renvoie. Lire une méthode pour la confier au thread principal
n'est pas noté : seul son appel touche le document. Lire un champ d'une struct
UNO ne l'est pas non plus : une struct est une copie, détachée du document.
Les opérateurs Python (`len`, `for`, `==`) ne sont pas relayés.
Le test vérifie ensuite que `violations` est vide : le code de production
avale souvent les exceptions, une erreur levée passerait inaperçue.
"""

import threading
from types import SimpleNamespace
from unittest.mock import NonCallableMock


class UnoStruct(SimpleNamespace):
    """Struct UNO (CellRangeAddress…) rendue par valeur par un faux objet."""


_PLAIN = (str, bytes, int, float, bool, type(None), dict, set, UnoStruct)


class ThreadGuard:
    def __init__(self, target, owner=None, violations=None, path="doc"):
        object.__setattr__(self, "_target", target)
        object.__setattr__(self, "_owner", owner or threading.current_thread())
        object.__setattr__(self, "violations", [] if violations is None else violations)
        object.__setattr__(self, "_path", path)

    def _check(self, path):
        current = threading.current_thread()
        if current is not self._owner:
            self.violations.append(f"{path} depuis {current.name}")

    def _guard(self, value, path):
        if type(value) in (list, tuple):
            return type(value)(self._guard(item, f"{path}[{i}]")
                               for i, item in enumerate(value))
        if isinstance(value, _PLAIN):
            return value
        return ThreadGuard(value, self._owner, self.violations, path)

    def __getattr__(self, name):
        path = f"{self._path}.{name}"
        value = getattr(self._target, name)
        if _is_value(name, value):
            self._check(path)
        return self._guard(value, path)

    def __setattr__(self, name, value):
        path = f"{self._path}.{name}"
        self._check(path)
        setattr(self._target, name, _unwrap(value))

    def __call__(self, *args, **kwargs):
        path = f"{self._path}()"
        self._check(path)
        args = [_unwrap(arg) for arg in args]
        kwargs = {key: _unwrap(value) for key, value in kwargs.items()}
        return self._guard(self._target(*args, **kwargs), path)


def _is_value(name, value):
    """Sur un mock, tout est appelable : la convention UNO tranche, les
    propriétés commencent par une majuscule et les méthodes par une minuscule."""
    if isinstance(value, NonCallableMock):
        return name[:1].isupper()
    return not callable(value)


def _unwrap(value):
    return value._target if isinstance(value, ThreadGuard) else value
