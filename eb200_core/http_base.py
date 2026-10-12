#!/usr/bin/env python3
"""
eb200_core/http_base.py

Gemeinsame HTTP-Serverbasis aller acht MoniScope-Server (T-1013): eine
ThreadingHTTPServer-Unterklasse, die harte Browser-Verbindungsabbrueche
still verwirft, statt sie als Traceback auf stderr zu drucken. Chromium
haelt fuer jede Seite mehrere "Reserve"-Sockets offen (Spekulativ-Verbindungen
fuer schnelleres Nachladen) und schliesst nicht benoetigte davon oft hart
(RST statt sauberem FIN) - unter Windows meldet sich das als WinError 10053/
10054, plattformuebergreifend als BrokenPipeError/ConnectionResetError/
ConnectionAbortedError. Ohne diese Klasse landet JEDER dieser Abbrueche als
vollstaendiger Traceback im Statusboard von start_eb200.py (siehe dort
_feed_error_detector) und im dortigen Fehlerzaehler - der ist dann
wertlos, weil er ueberwiegend Chromium-Marotten statt echter Fehler zaehlt.

Warum handle_error() ueberschreiben und NICHT try/except in den einzelnen
do_GET()/do_POST()-Methoden?
Ein Verbindungsabbruch kann auftreten, BEVOR irgendein Handler-Code laeuft -
naemlich schon beim Lesen der Request-Zeile selbst: http.server.
BaseHTTPRequestHandler.handle_one_request() ruft dafuer
self.rfile.readline() auf, WEIT VOR dem Aufruf von do_GET/do_POST. Ein
try/except in den Handler-Methoden faengt diesen Fall also grundsaetzlich
nicht ab. handle_error() dagegen wird vom socketserver-Unterbau
(BaseServer._handle_request_noblock, ueber ThreadingMixIn.process_request_
thread) bei JEDER unbehandelten Exception waehrend der gesamten
Anfrageverarbeitung aufgerufen, unabhaengig davon, ob sie beim Lesen der
Anfrage, beim Verarbeiten oder beim Schreiben der Antwort auftrat - das ist
die einzige Stelle, die alle drei Faelle einheitlich abdeckt.

Was hier still verworfen wird: BrokenPipeError, ConnectionResetError,
ConnectionAbortedError sowie ein blankes OSError mit winerror 10053
("Software caused connection abort") oder 10054 ("Connection reset by
peer") bzw. den plattformuebergreifenden errno-Aequivalenten (EPIPE/
ECONNRESET/ECONNABORTED) - siehe _is_client_abort(). Was NICHT verworfen
wird: alles andere, insbesondere ein Programmfehler (KeyError, ValueError,
...) im Handler-Code selbst - der erscheint weiterhin als vollstaendiger
Traceback auf stderr (Standardverhalten von handle_error()), denn genau
das soll das Statusboard weiterhin zuverlaessig anzeigen (PLAN.md T-1013).

Nur Python-Standardbibliothek.
"""

from __future__ import annotations

import errno
import http.server
import sys
import threading

# WinError-Nummern fuer einen harten Verbindungsabbruch, wie sie unter
# Windows manchmal ohne die spezifischeren OSError-Unterklassen ankommen.
_CLIENT_ABORT_WINERRORS = (10053, 10054)
# Plattformuebergreifende errno-Aequivalente (POSIX/WSL).
_CLIENT_ABORT_ERRNOS = (errno.EPIPE, errno.ECONNRESET, errno.ECONNABORTED)


def _is_client_abort(exc: BaseException) -> bool:
    """
    True fuer einen harten Client-Verbindungsabbruch (kein Programmfehler) -
    siehe Moduldocstring fuer die genaue Liste. Alles andere (insbesondere
    jede Exception, die kein OSError ist, oder ein OSError mit einem anderen
    errno/winerror) liefert False, damit ein echter Programmfehler weiterhin
    als Traceback sichtbar bleibt.
    """
    if isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
        return True
    if isinstance(exc, OSError):
        if getattr(exc, "winerror", None) in _CLIENT_ABORT_WINERRORS:
            return True
        if exc.errno in _CLIENT_ABORT_ERRNOS:
            return True
    return False


class QuietThreadingHTTPServer(http.server.ThreadingHTTPServer):
    """
    Ersetzt die bisher in jedem der acht Server wortgleich duplizierte
    "class ThreadingHTTPServer(http.server.ThreadingHTTPServer): daemon_
    threads = True; allow_reuse_address = True" - siehe Moduldocstring fuer
    das Warum von handle_error(). dropped_connections zaehlt verworfene
    Client-Abbrueche rein informativ mit (kein Fehlerzaehler im Sinne des
    Statusboards - der soll ja genau NICHT durch diese Faelle aufgeblaeht
    werden); der Zaehler ist wegen ThreadingMixIn (ein Thread je Anfrage)
    durch ein Lock geschuetzt.
    """
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._dropped_lock = threading.Lock()
        self.dropped_connections = 0

    def handle_error(self, request, client_address) -> None:
        exc = sys.exc_info()[1]
        if _is_client_abort(exc):
            with self._dropped_lock:
                self.dropped_connections += 1
            return
        super().handle_error(request, client_address)


# ---------------------------------------------------------------------------
# Selbsttest - "python eb200_core/http_base.py --selftest", nur Loopback,
# keine echte EB200-/SDR-Hardware noetig.
# ---------------------------------------------------------------------------

def _self_test() -> None:
    import contextlib
    import io
    import socket
    import struct
    import time

    # -- T1: _is_client_abort ------------------------------------------------
    assert _is_client_abort(BrokenPipeError()) is True
    assert _is_client_abort(ConnectionResetError()) is True
    assert _is_client_abort(ConnectionAbortedError()) is True
    win_exc = OSError()
    win_exc.winerror = 10054
    assert _is_client_abort(win_exc) is True
    win_exc2 = OSError()
    win_exc2.winerror = 10053
    assert _is_client_abort(win_exc2) is True
    assert _is_client_abort(ValueError("kein OSError")) is False
    assert _is_client_abort(OSError(errno.ENOENT, "nicht gefunden")) is False
    print("T1 ok")

    # -- T2: harter Client-Abbruch bleibt still, Server laeuft weiter -------
    class _MiniHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def do_GET(self):
            body = b"ok"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = QuietThreadingHTTPServer(("127.0.0.1", 0), _MiniHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        stderr_buf = io.StringIO()
        with contextlib.redirect_stderr(stderr_buf):
            # Harter Abbruch wie ein Chromium-Reserve-Socket: Verbindung
            # oeffnen, eine UNVOLLSTAENDIGE Anfrage senden (kein \r\n\r\n,
            # der Server haengt also noch in readline()) und dann per
            # SO_LINGER(1,0) hart schliessen - das erzeugt ein RST statt
            # eines sauberen FIN.
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect(("127.0.0.1", port))
            s.sendall(b"GET / HTTP/1.1")
            s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
            s.close()
            time.sleep(0.3)  # dem Server-Thread Zeit geben, den Abbruch zu verarbeiten

            with socket.create_connection(("127.0.0.1", port), timeout=2) as c:
                c.sendall(b"GET / HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
                resp = c.recv(4096)
        assert b"200" in resp, resp
        stderr_text = stderr_buf.getvalue()
        assert "Traceback" not in stderr_text, stderr_text
        # RST kann je nach Timing als Abbruch beim Lesen ankommen ODER (wenn
        # der Server noch gar nicht bis zum Lesen gekommen ist) bereits vom
        # Betriebssystem verworfen werden, bevor handle_error() ueberhaupt
        # gerufen wird - deshalb hier kein exakter erwarteter Wert, nur die
        # Typpruefung (nie negativ) und vor allem: kein Traceback, Server
        # antwortet der naechsten Anfrage weiterhin mit 200.
        assert server.dropped_connections >= 0
        print("T2 ok")
    finally:
        server.shutdown()
        server.server_close()

    # -- T3: echter Programmfehler bleibt sichtbar ---------------------------
    class _BuggyHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def do_GET(self):
            if self.path == "/boom":
                raise ValueError("Absichtlicher Selbsttest-Fehler")
            body = b"ok"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server2 = QuietThreadingHTTPServer(("127.0.0.1", 0), _BuggyHandler)
    port2 = server2.server_address[1]
    thread2 = threading.Thread(target=server2.serve_forever, daemon=True)
    thread2.start()
    try:
        stderr_buf2 = io.StringIO()
        with contextlib.redirect_stderr(stderr_buf2):
            try:
                with socket.create_connection(("127.0.0.1", port2), timeout=2) as c:
                    c.sendall(b"GET /boom HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
                    c.recv(4096)
            except OSError:
                pass  # Server schliesst die Verbindung ohne Antwort - erwartet
            time.sleep(0.2)
        stderr_text2 = stderr_buf2.getvalue()
        assert "Traceback" in stderr_text2, "Programmfehler haette einen Traceback erzeugen muessen"
        assert "ValueError" in stderr_text2, stderr_text2

        # Naechste Anfrage (anderer Pfad) -> Server laeuft unveraendert weiter.
        with socket.create_connection(("127.0.0.1", port2), timeout=2) as c2:
            c2.sendall(b"GET / HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
            resp2 = c2.recv(4096)
        assert b"200" in resp2, resp2
        print("T3 ok")
    finally:
        server2.shutdown()
        server2.server_close()

    print("Selbsttest OK")


def main() -> int:
    import argparse

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    ap = argparse.ArgumentParser(description="Gemeinsame HTTP-Serverbasis (T-1013)")
    ap.add_argument("--selftest", action="store_true", help="Selbsttest ausfuehren")
    args = ap.parse_args()

    if args.selftest:
        _self_test()
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
