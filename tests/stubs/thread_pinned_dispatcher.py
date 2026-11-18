"""Faux thread principal pour les tests de marshalling."""

import queue
import threading


class ThreadPinnedDispatcher:
    """Dispatcher qui exécute réellement sur UN thread désigné.

    Il tient le rôle du thread principal de LibreOffice : une file, un thread
    de service, et des appels qui attendent leur résultat. C'est ce qui permet
    de prouver le marshalling au lieu de le supposer.
    """

    def __init__(self):
        self._queue = queue.Queue()
        self._closed = False
        self.thread = threading.Thread(target=self._serve, name="main-uno",
                                       daemon=True)
        self.thread.start()

    def _serve(self):
        while True:
            item = self._queue.get()
            if item is None:
                return
            fn, result_queue = item
            try:
                value = fn()
                if result_queue is not None:
                    result_queue.put(("ok", value))
            except Exception as exc:
                if result_queue is not None:
                    result_queue.put(("error", exc))

    def post(self, fn):
        if self._closed:
            return False
        self._queue.put((fn, None))
        return True

    def call(self, fn, timeout=30.0):
        if self._closed:
            raise RuntimeError("fermé")
        result_queue = queue.Queue(maxsize=1)
        self._queue.put((fn, result_queue))
        status, payload = result_queue.get(timeout=timeout)
        if status == "error":
            raise payload
        return payload

    def close(self):
        self._closed = True
        self._queue.put(None)
