"""Hostile-server timing: hanging, dripping and rate-limiting sites must cost a bounded time per request."""
import socket
import socketserver
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from fjordlens import net

STOP = threading.Event()


class Handler(socketserver.BaseRequestHandler):
    mode = "ok"

    def handle(self):
        try:
            request = self.request.recv(4096).decode("latin-1")
            path = request.split(" ")[1] if " " in request else "/"
            mode = self.server.mode
            if path == "/robots.txt" and mode != "hang":
                self.request.sendall(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                return
            if mode == "hang":
                STOP.wait(30)
            elif mode == "drip_headers":
                self.request.sendall(b"HTTP/1.1 200 OK\r\n")
                while not STOP.wait(0.3):
                    self.request.sendall(b"X")
            elif mode == "drip_body":
                self.request.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: 100000\r\n\r\n")
                while not STOP.wait(0.3):
                    self.request.sendall(b"a")
            elif mode == "trailer_drip":
                # "Connection: close" makes http.client drop the socket from the connection; the response
                # keeps reading endless trailer lines, each well inside the socket timeout.
                self.request.sendall(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n1\r\na\r\n0\r\n")
                while not STOP.wait(0.3):
                    self.request.sendall(b"X-T: y\r\n")
            elif mode == "ok_length":
                body = b"<html><title>OK</title><p>Hei</p></html>"
                self.request.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nConnection: close\r\nContent-Length: "
                                     + str(len(body)).encode() + b"\r\n\r\n" + body)
            elif mode == "ok_chunked":
                self.request.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
                                     b"5\r\nHello\r\n6\r\n world\r\n0\r\n\r\n")
            elif mode == "503":
                self.request.sendall(b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            elif mode == "429":
                self.request.sendall(b"HTTP/1.1 429 Too Many Requests\r\nRetry-After: 3600\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        except OSError:
            pass


class Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


class HostileServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        STOP.clear()
        cls.server = Server(("127.0.0.1", 0), Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        STOP.set()
        cls.server.shutdown()
        cls.server.server_close()

    def fetch(self, mode, timeout=2, **kwargs):
        self.server.mode = mode
        port = self.port

        def connect(conn):
            conn.sock = socket.create_connection(("127.0.0.1", port), conn.timeout)

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(net, "resolve_public", lambda host, p: "127.0.0.1"), \
                mock.patch.object(net.PinnedHTTP, "connect", connect):
            fetcher = net.Fetcher(Path(tmp), net.Budget(100, 300, 100), timeout=timeout)
            started = time.monotonic()
            result = fetcher.get(f"http://{mode.replace('_', '-')}.example.no/", "923609016", **kwargs)
            self.receipts = [r for r in fetcher.budget.receipts if "robots.txt" not in r["url"]]
            return result, time.monotonic() - started

    def test_hanging_site_is_abandoned(self):
        result, elapsed = self.fetch("hang")
        self.assertNotEqual(result.state, "available")
        self.assertLess(elapsed, 9.0)  # robots timeout + one patient retry (1.5x), never the server's 30 s

    def test_header_drip_is_bounded(self):
        result, elapsed = self.fetch("drip_headers")
        self.assertNotEqual(result.state, "available")
        self.assertLess(elapsed, 8.0)

    def test_body_drip_is_bounded(self):
        result, elapsed = self.fetch("drip_body")
        self.assertNotEqual(result.state, "available")
        self.assertLess(elapsed, 8.0)

    def test_trailer_drip_after_connection_close_is_bounded(self):
        result, elapsed = self.fetch("trailer_drip")
        self.assertNotEqual(result.state, "available")
        self.assertEqual(result.error, "Request exceeded its total time limit")  # stopped by the watchdog itself
        self.assertLess(elapsed, 8.0)

    def test_normal_responses_still_succeed(self):
        # Positive controls: the watchdog and socket handling must not break ordinary complete responses.
        for mode, body in (("ok_length", b"<html><title>OK</title><p>Hei</p></html>"), ("ok_chunked", b"Hello world")):
            with self.subTest(mode=mode):
                result, elapsed = self.fetch(mode)
                self.assertEqual((result.state, result.status, result.body), ("available", 200, body))
                self.assertLess(elapsed, 4.0)

    def test_retry_of_a_paid_request_is_billed_again(self):
        result, _ = self.fetch("503", headers={"X-Test": "1"}, cost=0.25)
        self.assertEqual(result.status, 503)
        self.assertEqual([r.get("declared_cost_usd") for r in self.receipts], [0.25, 0.25])

    def test_site_rate_limit_with_long_retry_after_is_not_waited_out(self):
        result, elapsed = self.fetch("429")
        self.assertEqual(result.status, 429)
        self.assertLess(elapsed, 4.0)


if __name__ == "__main__":
    unittest.main()
