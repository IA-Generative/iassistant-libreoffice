"""Flux du modèle coupé par le réseau, lu par le vrai urllib.

Une connexion fermée en pleine réponse ne lève rien côté urllib : la lecture
s'arrête comme si la réponse était complète. Seul un serveur réel le montre ;
un faux flux en mémoire n'a pas de connexion à perdre.
"""

import json
import socket
import threading
import urllib.request

from src.mirai.core import sse_pump
from tests.stubs.fake_shell import FakeShell


def _serve_then_close(lines, ready):
    """Répond en flux chunké, puis ferme la connexion au milieu de la réponse."""
    server = socket.create_server(("127.0.0.1", 0))
    ready.append(server.getsockname()[1])
    conn, _ = server.accept()
    conn.recv(65536)
    conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                 b"Transfer-Encoding: chunked\r\n\r\n")
    for line in lines:
        body = line + b"\n\n"
        conn.sendall(b"%x\r\n" % len(body) + body + b"\r\n")
    conn.shutdown(socket.SHUT_RDWR)
    conn.close()
    server.close()


class _NetworkShell(FakeShell):
    def urlopen(self, request, timeout=None):
        return urllib.request.urlopen(request, timeout=timeout)


def test_a_connection_closed_during_the_reasoning_is_a_network_error():
    reasoning = b"data: " + json.dumps(
        {"choices": [{"delta": {"reasoning_content": "Je pèse le ton."}}]}).encode()
    ready = []
    server = threading.Thread(target=_serve_then_close, args=([reasoning] * 3, ready),
                              daemon=True)
    server.start()
    while not ready:
        pass
    shell = _NetworkShell()

    outcome = sse_pump.run_stream(shell, f"http://127.0.0.1:{ready[0]}/", lambda e: None)
    server.join(5)

    assert not outcome.ok
    assert isinstance(outcome.error, sse_pump.StreamNetworkError)
