"""Faux service AsyncCallback : les tâches attendent qu'on joue la boucle d'événements."""


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
            task.notify(None)


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
