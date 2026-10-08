"""`pump_events` : pomper hors du thread principal doit être un no-op, jamais un abort.

Un thread de fond qui appelle `processEventsToIdle()` meurt en tenant le
SolarMutex (abort dans DispatchUserEvents) : le thread principal reste bloqué et
LibreOffice ne répond plus. Ce n'est pas une lenteur, c'est un interblocage
définitif. Incident et prélèvement de pile : docs/RAPPORT-EXECUTION-2026-07-26.md.

Ces tests garantissent que la garde reste en place.
"""

import threading

from tests.stubs.uno_stubs import install

install()   # doit précéder l'import d'entrypoint (qui importe uno/unohelper)

from src.mirai.entrypoint import is_main_thread, pump_events


class FakeToolkit:
    """Toolkit qui note chaque pompage et le thread d'où il vient."""

    def __init__(self):
        self.threads = []

    def processEventsToIdle(self):
        self.threads.append(threading.current_thread())


def test_pumps_on_the_main_thread():
    toolkit = FakeToolkit()
    assert pump_events(toolkit) is True
    assert len(toolkit.threads) == 1


def test_never_pumps_from_a_background_thread():
    """Le test qui compte : c'est cet appel-là qui gelait LibreOffice."""
    toolkit = FakeToolkit()
    result = {}

    def worker():
        result["returned"] = pump_events(toolkit)

    thread = threading.Thread(target=worker, name="faux-worker")
    thread.start()
    thread.join(timeout=5)

    assert toolkit.threads == [], (
        "processEventsToIdle a été appelé depuis un thread de fond — "
        "c'est un abort de LibreOffice, pas un ralentissement")
    assert result["returned"] is False


def test_none_toolkit_is_tolerated():
    """Le toolkit peut être indisponible (contexte dégradé) : pas d'exception."""
    assert pump_events(None) is False


def test_toolkit_failure_is_swallowed():
    """Un contrôle disposé pendant la fermeture ne doit pas remonter."""

    class Broken:
        def processEventsToIdle(self):
            raise RuntimeError("peer détruit")

    assert pump_events(Broken()) is False


def test_is_main_thread_discriminates():
    assert is_main_thread() is True

    seen = {}
    thread = threading.Thread(target=lambda: seen.update(v=is_main_thread()))
    thread.start()
    thread.join(timeout=5)
    assert seen["v"] is False


def test_no_raw_pump_call_remains_in_the_shell():
    """Garde-fou : tout pompage passe par pump_events(), sans exception.

    Un appel direct rétabli quelque part suffirait à ramener l'interblocage.
    """
    import ast
    import os

    path = os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "src", "mirai", "entrypoint.py")
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())

    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "processEventsToIdle":
            offenders.append(node.lineno)

    # Une seule occurrence légitime : celle qui vit DANS pump_events.
    assert len(offenders) <= 1, (
        f"appels directs à processEventsToIdle hors de pump_events : lignes {offenders}")
