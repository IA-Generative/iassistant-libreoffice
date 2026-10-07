"""MainThreadDispatcher : marshalling vers le thread principal.

Ces tests tournent hors LibreOffice. Le service AsyncCallback est simulé par un
faux qui garde les tâches en file et ne les exécute que lorsqu'on le lui demande
— ce qui permet de vérifier l'asynchronisme réel de `post()` et le timeout de
`call()` sans dépendre du minutage.
"""

import threading

import pytest

from src.mirai.core.ui_thread import (
    DirectDispatcher,
    DispatcherClosed,
    DispatcherTimeout,
    MainThreadDispatcher,
    is_main_thread,
)


class FakeAsyncCallback:
    """Imite com.sun.star.awt.AsyncCallback : accumule, exécute sur demande."""

    def __init__(self):
        self.queued = []

    def addCallback(self, task, _data):
        self.queued.append(task)

    def drain(self):
        """Joue les tâches en attente — ce que ferait la boucle d'événements."""
        pending, self.queued = self.queued, []
        for task in pending:
            task.run()


class FakeUnoContext:
    def __init__(self, callback=None, fail=False):
        self._callback = callback
        self._fail = fail

    def getServiceManager(self):
        return self

    def createInstanceWithContext(self, name, _ctx):
        assert name == "com.sun.star.awt.AsyncCallback"
        if self._fail:
            raise RuntimeError("service indisponible")
        return self._callback


def _dispatcher(fail=False):
    callback = FakeAsyncCallback()
    return MainThreadDispatcher(FakeUnoContext(callback, fail=fail)), callback


def test_post_from_the_main_thread_runs_immediately():
    """Déjà au bon endroit : inutile de faire un détour par la file."""
    dispatcher, _callback = _dispatcher()
    seen = []

    assert dispatcher.post(lambda: seen.append("fait")) is True
    assert seen == ["fait"]


def test_post_from_a_worker_waits_for_the_pump():
    """Depuis un thread de fond, la tâche attend d'être drainée.

    C'est tout l'objet de la pompe : un AsyncCallback émis depuis un worker est
    mis en file mais ne RÉVEILLE pas la boucle d'événements de LibreOffice.
    """
    dispatcher, _callback = _dispatcher()
    seen = []

    thread = threading.Thread(
        target=lambda: dispatcher.post(lambda: seen.append("fait")))
    thread.start()
    thread.join(timeout=5)

    assert seen == [], "la tâche ne doit pas s'exécuter dans le worker"
    dispatcher.drain()
    assert seen == ["fait"]


def test_post_avale_les_exceptions():
    dispatcher, _callback = _dispatcher()

    def boom():
        raise ValueError("panne d'affichage")

    thread = threading.Thread(target=lambda: dispatcher.post(boom))
    thread.start()
    thread.join(timeout=5)
    dispatcher.drain()  # ne propage pas : un affichage raté n'est pas fatal


def test_post_devient_inerte_apres_close():
    dispatcher, _callback = _dispatcher()
    seen = []

    dispatcher.close()
    assert dispatcher.post(lambda: seen.append("fait")) is False
    dispatcher.drain()
    assert seen == [], "après close(), plus rien ne doit s'exécuter"


def test_pump_without_asynccallback_degrades_to_direct_drain():
    """Dégradation gracieuse : service absent → drain immédiat."""
    dispatcher, _ = _dispatcher(fail=True)
    seen = []

    thread = threading.Thread(
        target=lambda: dispatcher.post(lambda: seen.append("fait")))
    thread.start()
    thread.join(timeout=5)
    dispatcher.start_pump()
    assert seen == ["fait"]


def test_call_rapporte_le_resultat():
    dispatcher, _callback = _dispatcher()
    result = {}

    def worker():
        result["value"] = dispatcher.call(lambda: 6 * 7, timeout=5)

    thread = threading.Thread(target=worker)
    thread.start()
    _drain_until(dispatcher, thread)
    thread.join(timeout=5)

    assert result["value"] == 42


def test_call_propage_lexception_de_fn():
    dispatcher, _callback = _dispatcher()
    captured = {}

    def worker():
        try:
            dispatcher.call(_raise_key_error, timeout=5)
        except KeyError as exc:
            captured["exc"] = exc

    thread = threading.Thread(target=worker)
    thread.start()
    _drain_until(dispatcher, thread)
    thread.join(timeout=5)

    assert isinstance(captured.get("exc"), KeyError)


def test_call_leve_timeout_si_personne_ne_draine():
    """Pompe arrêtée ou thread principal retenu : on abandonne, on ne gèle pas.

    Depuis un worker et sans drain, l'appel doit rendre la main — attendre
    indéfiniment laisserait le run suspendu pour toujours.
    """
    dispatcher, _callback = _dispatcher()
    captured = {}

    def worker():
        try:
            dispatcher.call(lambda: "jamais atteint", timeout=0.15)
        except DispatcherTimeout as exc:
            captured["exc"] = exc

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(timeout=5)

    assert isinstance(captured.get("exc"), DispatcherTimeout)


def test_call_from_the_main_thread_runs_immediately():
    """Sur le thread principal, aucun détour : ni file, ni attente."""
    dispatcher, _callback = _dispatcher()
    assert dispatcher.call(lambda: 21 * 2, timeout=0.1) == 42


def test_call_leve_closed_apres_fermeture():
    dispatcher, _ = _dispatcher()
    dispatcher.close()

    with pytest.raises(DispatcherClosed):
        dispatcher.call(lambda: 1, timeout=1)


def test_direct_dispatcher_est_synchrone():
    dispatcher = DirectDispatcher()
    seen = []

    dispatcher.post(lambda: seen.append("post"))
    assert dispatcher.call(lambda: "call") == "call"
    assert seen == ["post"]


def test_direct_dispatcher_respecte_close():
    dispatcher = DirectDispatcher()
    dispatcher.close()

    assert dispatcher.post(lambda: None) is False
    with pytest.raises(DispatcherClosed):
        dispatcher.call(lambda: None)


def test_is_main_thread():
    assert is_main_thread() is True

    seen = {}
    thread = threading.Thread(target=lambda: seen.update(value=is_main_thread()))
    thread.start()
    thread.join(timeout=5)
    assert seen["value"] is False


def _raise_key_error():
    raise KeyError("absent")


def _drain_until(dispatcher, thread, attempts=300):
    """Joue le rôle de la boucle d'événements pendant que le worker attend."""
    for _ in range(attempts):
        dispatcher.drain()
        if not thread.is_alive():
            dispatcher.drain()
            return
        threading.Event().wait(0.01)
