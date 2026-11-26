"""Faux réseau placé sous la coquille.

Tout appel HTTP de l'extension finit dans `urllib.request.OpenerDirector.open`,
quel que soit le module qui a préparé la requête. L'intercepter là montre la
requête telle qu'elle partirait (proxy et contexte TLS compris), sans citer un
membre de `MainJob` : un test écrit ainsi survit à l'extraction du transport.
"""

import io
import json
import ssl
import threading
import urllib.error
import urllib.request


class SentRequest:
    """Une requête vue par le faux réseau."""

    def __init__(self, opener, request, timeout):
        self.method = request.get_method()
        self.url = request.full_url
        self.body = request.data
        self.timeout = timeout
        self.on_main_thread = threading.current_thread() is threading.main_thread()
        self._headers = {name.lower(): value for name, value in request.header_items()}
        # Une table de proxys par ProxyHandler de l'opener, dans l'ordre où
        # urllib les consulte : vide quand la requête part en direct.
        self.proxies = []
        self.proxy_challenges_answered = set()
        self.tls = None
        for handler in opener.handlers:
            if isinstance(handler, urllib.request.ProxyHandler):
                self.proxies.append(dict(handler.proxies))
            if isinstance(handler, urllib.request.ProxyBasicAuthHandler):
                self.proxy_challenges_answered.add("Basic")
            if isinstance(handler, urllib.request.ProxyDigestAuthHandler):
                self.proxy_challenges_answered.add("Digest")
            if isinstance(handler, urllib.request.HTTPSHandler):
                self.tls = handler._context

    def header(self, name):
        """Valeur de l'en-tête, sans tenir compte de la casse ; None s'il est absent."""
        return self._headers.get(name.lower())

    @property
    def verifies_certificates(self):
        return self.tls is None or self.tls.verify_mode == ssl.CERT_REQUIRED

    def json(self):
        return json.loads(self.body.decode("utf-8"))

    def __repr__(self):
        return f"<{self.method} {self.url}>"


class _Response(io.BytesIO):
    def __init__(self, body, status, headers):
        super().__init__(body)
        self.status = status
        self.headers = dict(headers or {})


def _encode(body):
    if body is None:
        return b""
    if isinstance(body, bytes):
        return body
    if isinstance(body, str):
        return body.encode("utf-8")
    return json.dumps(body).encode("utf-8")


class FakeNetwork:
    """Répond aux requêtes par routes et garde la trace de ce qui est parti.

    `system_proxies` tient lieu des proxys du système (variables d'environnement,
    réglages macOS ou Windows) que `urllib` applique quand on ne lui en donne pas.
    """

    def __init__(self):
        self.sent = []
        self.unexpected = []
        self.system_proxies = {}
        self._routes = []

    def install(self, monkeypatch):
        def open_(opener, request, data=None, timeout=None):
            return self._answer(opener, request, timeout)

        monkeypatch.setattr(urllib.request.OpenerDirector, "open", open_)
        monkeypatch.setattr(urllib.request, "getproxies", lambda: dict(self.system_proxies))

    def on(self, method, url_part, body=None, status=200, headers=None, error=None):
        """Réponse à une requête `method` dont l'URL contient `url_part`.

        Plusieurs appels pour la même route donnent des réponses successives ; la
        dernière sert ensuite indéfiniment. `status` >= 400 lève l'erreur HTTP
        correspondante, `error` lève l'exception donnée (panne réseau)."""
        answer = (_encode(body), status, headers, error)
        for route in self._routes:
            if route[0] == method and route[1] == url_part:
                route[2].append(answer)
                return
        self._routes.append((method, url_part, [answer]))

    def requests(self, method, url_part):
        return [sent for sent in self.sent
                if sent.method == method and url_part in sent.url]

    def _answer(self, opener, request, timeout):
        if not isinstance(request, urllib.request.Request):
            request = urllib.request.Request(request)
        sent = SentRequest(opener, request, timeout)
        self.sent.append(sent)
        for method, url_part, answers in self._routes:
            if method == sent.method and url_part in sent.url:
                body, status, headers, error = answers.pop(0) if len(answers) > 1 else answers[0]
                if error is not None:
                    raise error
                if status >= 400:
                    raise urllib.error.HTTPError(
                        sent.url, status, "réponse du faux réseau", headers or {}, io.BytesIO(body))
                return _Response(body, status, headers)
        self.unexpected.append(sent)
        raise urllib.error.URLError(f"faux réseau : aucune route pour {sent.method} {sent.url}")
