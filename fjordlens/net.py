"""Counted, bounded HTTP with public-IP pinning, TLS verification and robots."""
from __future__ import annotations

import gzip
import http.client
import io
import ipaddress
import json
import math
import os
import re
import socket
import ssl
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

from .core import digest, now, write_json
from .robots import Robots

AGENT = "FjordLens/0.2 (+public company research; robots.txt respected)"
ROBOTS_TOKEN = "FjordLens"
RESTRICTED = {"linkedin.com", "facebook.com", "instagram.com", "glassdoor.com", "indeed.com", "google.com", "tiktok.com", "x.com", "twitter.com"}
OFFICIAL = {"data.brreg.no"}
HOST_INTERVALS = {"pam-stilling-feed.nav.no": 0.25,  # Documented bulk API; politely paced.
                  "api.search.brave.com": 1.05}  # Brave's base plans allow about one query per second.

class FetchError(Exception):
    pass

def utc_timestamp(value):
    """Require an explicit timezone; compare instants rather than ISO strings."""
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Evidence cutoff must include a timezone")
    return parsed.astimezone(timezone.utc)

def public_unicast(value):
    ip = ipaddress.ip_address(value)
    if not ip.is_global or ip.is_multicast or ip.is_reserved:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        if ip in ipaddress.ip_network("64:ff9b::/96") or ip in ipaddress.ip_network("2002::/16"):
            return False
        if ip.ipv4_mapped and not public_unicast(str(ip.ipv4_mapped)):
            return False
    return True

def safe_url(url: str) -> str:
    if not isinstance(url, str) or len(url) > 4096 or any(ord(c) < 32 for c in url) or "\\" in url:
        raise FetchError("Malformed URL")
    p = urllib.parse.urlsplit(url)
    if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password or p.port not in {None, 80, 443}:
        raise FetchError("Only public HTTP(S) URLs on standard ports are allowed")
    host = p.hostname.encode("idna").decode("ascii").lower().rstrip(".")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")) or "." not in host:
        raise FetchError("Local host blocked")
    try:
        addr = ipaddress.ip_address(host)
        if not public_unicast(host):
            raise FetchError("Non-public address blocked")
    except ValueError:
        pass
    netloc = host + (f":{p.port}" if p.port else "")
    return urllib.parse.urlunsplit((p.scheme, netloc, urllib.parse.quote(urllib.parse.unquote(p.path or "/"), safe="/%:@!$&'()*+,;=-._~"), p.query, ""))

def resolve_public(host: str, port: int) -> str:
    results = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses = sorted({r[4][0] for r in results}, key=lambda a: ":" in a)
    if not addresses or any(not public_unicast(ip) for ip in addresses):
        raise FetchError("DNS resolved to a non-public address")
    return addresses[0]

def _abort(connection, fired, live):
    """Watchdog: a server that trickles bytes can keep every single recv inside the socket timeout, so a
    request also has a hard total deadline; shutting the socket down wakes the blocked read. With
    "Connection: close" the response keeps the socket after the connection drops it, so use the saved one."""
    fired.set()
    sock = live.get("sock") or getattr(connection, "sock", None)
    if sock is not None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


class PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, host, ip, port, timeout):
        super().__init__(host, port, timeout=timeout)
        self.ip = ip
    def connect(self):
        self.sock = socket.create_connection((self.ip, self.port), self.timeout)

class PinnedHTTPS(PinnedHTTP):
    default_port = 443
    def connect(self):
        super().connect()
        self.sock = ssl.create_default_context().wrap_socket(self.sock, server_hostname=self.host)

@dataclass
class Response:
    requested_url: str
    url: str
    status: int = 0
    body: bytes = b""
    headers: dict = field(default_factory=dict)
    retrieved_at: str = field(default_factory=now)
    redirects: list = field(default_factory=list)
    error: str | None = None
    source_class: str = "company_owned"
    storage_path: str | None = None
    policy: str = "robots_checked_public_page"
    cache_hit: bool = False

    @property
    def sha256(self):
        return digest(self.body)
    @property
    def snapshot_id(self):
        return "ss_" + digest([self.url, self.sha256, self.retrieved_at, self.status])[:24]
    @property
    def state(self):
        if self.error:
            if re.search(r"getaddrinfo failed|Name or service not known|nodename nor servname|No address associated", self.error):
                return "not_available"  # The host does not exist in DNS: nothing is there.
            return "blocked" if self.error.startswith(("Policy:", "Robots:", "Budget:", "Cutoff:")) else "failed"
        if self.status == 200:
            return "available"
        if self.status in {404, 410}:
            return "not_available"
        return "blocked" if self.status in {401, 403, 429, 451} else "failed"
    def text(self):
        match = re.search(r"charset\s*=\s*['\"]?([a-zA-Z0-9_-]+)", self.headers.get("content-type", ""))
        charset = match.group(1) if match else "utf-8"
        try:
            return self.body.decode(charset, "replace")
        except LookupError:
            return self.body.decode("utf-8", "replace")
    def json(self):
        try:
            return json.loads(self.body)
        except (ValueError, UnicodeError):
            return None
    def metadata(self):
        return {"id": self.snapshot_id, "requested_url": self.requested_url, "final_url": self.url,
                "http_status": self.status, "retrieved_at": self.retrieved_at, "content_sha256": self.sha256,
                "redirect_chain": self.redirects, "source_class": self.source_class, "access_policy": self.policy,
                "storage_path": self.storage_path, "content_type": self.headers.get("content-type"),
                "last_modified": self.headers.get("last-modified"), "etag": self.headers.get("etag"),
                "body_size_bytes": len(self.body), "cache_hit": self.cache_hit, "error": self.error}

def snapshot_extension(content_type):
    """File extension for a stored response body, from its declared content type."""
    kind = str(content_type or "").split(";", 1)[0].strip().lower()
    if "html" in kind:
        return "html"
    if "json" in kind:
        return "json"
    if "xml" in kind or "rss" in kind or "atom" in kind:
        return "xml"
    if kind.startswith("text/"):
        return "txt"
    if kind == "application/pdf":
        return "pdf"
    return "bin"


def read_snapshot_bytes(path):
    """Captured body bytes; accepts the raw files written now and gzip files written by earlier versions."""
    data = Path(path).read_bytes()
    return gzip.decompress(data) if str(path).endswith(".gz") else data


class Budget:
    def __init__(self, requests=2000, seconds=2600, per_company=20):
        self.max_requests, self.deadline, self.per_company = requests, time.monotonic() + seconds, per_company
        try:
            self.max_cost = float(os.environ.get("FJORDLENS_MAX_API_COST_USD") or 9.0)
        except ValueError:
            self.max_cost = 9.0
        if not math.isfinite(self.max_cost) or self.max_cost < 0:
            self.max_cost = 9.0
        self.lock = threading.RLock()
        self.total = 0
        self.counts = {}
        self.cost = 0.0
        self.receipts = []

    def close(self):
        """Refuse every further request (watchdog finalization)."""
        with self.lock:
            self.deadline = min(self.deadline, time.monotonic())

    def reserve(self, org, url, cost=0.0):
        if not isinstance(cost, (int, float)) or isinstance(cost, bool) or not math.isfinite(cost) or cost < 0:
            raise FetchError("Budget: invalid declared request cost")
        with self.lock:
            if time.monotonic() >= self.deadline:
                raise FetchError("Budget: wall-clock deadline reached")
            # Shared run-level sources (e.g. the job-feed catch-up) count toward the total only.
            if self.total >= self.max_requests or (org != "_shared" and self.counts.get(org, 0) >= self.per_company):
                raise FetchError("Budget: request allowance exhausted")
            if self.cost + cost > self.max_cost:
                raise FetchError("Budget: external API spend ceiling")
            self.total += 1
            self.counts[org] = self.counts.get(org, 0) + 1
            self.cost += cost
            # The request log keeps a search endpoint without its query (search terms are not retained).
            logged = url.split("?", 1)[0] if "api.search.brave.com" in url else url
            receipt = {"sequence": self.total, "organisation_number": org, "url": logged,
                       "started_at": now(), "declared_cost_usd": cost}
            self.receipts.append(receipt)
            return receipt

class Fetcher:
    def __init__(self, root: Path, budget: Budget, timeout=12, replay: Path | None = None, cutoff=None, registry_snapshot=None):
        self.root, self.budget, self.timeout = root, budget, timeout
        self.replay, self.cutoff = replay, cutoff
        self.cutoff_instant = utc_timestamp(cutoff) if cutoff else None
        self.cache = OrderedDict()  # Byte-bounded LRU; bodies are also saved to disk.
        self.cache_bytes, self.cache_limit = 0, int(os.environ.get("FJORDLENS_CACHE_BYTES", 256_000_000))
        self.robots = {}
        self.locks = {}
        self.lock = threading.RLock()
        self.last_request = {}
        self.body_limit = 3_000_000
        self.dns_enabled = replay is None  # Name-derived domain discovery needs live DNS.
        self.caches = {}
        self.official_interval = float(os.environ.get("FJORDLENS_OFFICIAL_INTERVAL", "0.1"))
        self.site_interval = float(os.environ.get("FJORDLENS_SITE_INTERVAL", "0.5"))
        self.registry_fetcher = Fetcher(root, budget, replay=Path(registry_snapshot), cutoff=cutoff) if registry_snapshot else None

    def _cache_get(self, key):
        with self.lock:
            result = self.cache.get(key)
            if result is not None:
                self.cache.move_to_end(key)
            return result

    def _cache_put(self, key, result):
        size = len(result.body or b"")
        if size > 1_000_000:
            return
        with self.lock:
            old = self.cache.pop(key, None)
            if old is not None:
                self.cache_bytes -= len(old.body or b"")
            self.cache[key] = result
            self.cache_bytes += size
            while self.cache_bytes > self.cache_limit and self.cache:
                _, evicted = self.cache.popitem(last=False)
                self.cache_bytes -= len(evicted.body or b"")

    def host_lock(self, key):
        with self.lock:
            return self.locks.setdefault(key, threading.RLock())

    def _save(self, response):
        # The captured body is stored byte-for-byte, named by its SHA-256, so any verifier can hash the file at
        # `snapshot_path` and compare it with `content_sha256` (Builderr's citation-validator contract).
        folder = self.root / "snapshots"
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / (response.sha256 + "." + snapshot_extension(response.headers.get("content-type")))
        with self.host_lock(response.sha256):
            if not dest.exists():
                dest.write_bytes(response.body)
        response.storage_path = "snapshots/" + dest.name
        record = response.metadata()
        record["headers"] = {k: v for k, v in response.headers.items() if k in {"content-type", "etag", "last-modified", "date"}}
        write_json(folder / (response.snapshot_id + ".json"), record)

    def _raw(self, url, org, source_class, headers=None, cost=0.0, timeout=None):
        limit = timeout or self.timeout
        receipt = None
        connection = None
        watchdog, fired, live = None, threading.Event(), {}
        try:
            url = safe_url(url)
            if time.monotonic() + limit > self.budget.deadline:
                raise FetchError("Budget: insufficient time for another request")
            p = urllib.parse.urlsplit(url)
            host = p.hostname
            if any(host == h or host.endswith("." + h) for h in RESTRICTED):
                # Enforced on every request, including robots.txt redirect hops.
                raise FetchError("Restricted platform; a licensed connector is required")
            ip = resolve_public(host, p.port or (443 if p.scheme == "https" else 80))
            # Resolve once and connect to that exact public IP. SNI/Host use the original host.
            # Separate rate lanes: the rate-limited filing-copy endpoint must never stall the
            # other official endpoints on the same host.
            lane = host + ("#filing-copies" if "/aarsregnskap/kopi/" in p.path else "")
            with self.host_lock("rate:" + lane):
                interval = 1.1 if lane.endswith("#filing-copies") else (self.official_interval if host in OFFICIAL else HOST_INTERVALS.get(host, self.site_interval))
                cached = self.robots.get(p.scheme + "://" + p.netloc)
                if cached and cached[0].state == "parsed":
                    interval = max(interval, cached[0].crawl_delay(ROBOTS_TOKEN) or 0)
                delay = max(0, self.last_request.get(lane, 0) + interval - time.monotonic())
                if time.monotonic() + delay + limit > self.budget.deadline:
                    raise FetchError("Budget: insufficient time for another request")
                if delay:
                    time.sleep(delay)
                receipt = self.budget.reserve(org, url, cost)
                receipt["pinned_ip"] = ip
                self.last_request[lane] = time.monotonic()
            connection_type = PinnedHTTPS if p.scheme == "https" else PinnedHTTP
            connection = connection_type(host, ip, p.port or (443 if p.scheme == "https" else 80), limit)
            total = max(0.5, min(2.0 * limit, self.budget.deadline - time.monotonic()))
            watchdog = threading.Timer(total, _abort, args=(connection, fired, live))
            watchdog.daemon = True
            watchdog.start()
            request_headers = {"User-Agent": AGENT, "Accept": "application/json,text/html,application/xml;q=0.9,*/*;q=0.5", "Accept-Encoding": "gzip", "Connection": "close"}
            request_headers.update(headers or {})
            connection.request("GET", p.path + ("?" + p.query if p.query else ""), headers=request_headers)
            live["sock"] = connection.sock
            if fired.is_set():
                raise FetchError("Request exceeded its total time limit")
            raw = connection.getresponse()
            read_deadline = min(time.monotonic() + limit, self.budget.deadline)
            chunks, length = [], 0
            while length <= self.body_limit:
                remaining = read_deadline - time.monotonic()
                if remaining <= 0:
                    raise FetchError("Response body read deadline exceeded")
                sock = live.get("sock") or connection.sock
                if sock:
                    try:
                        sock.settimeout(remaining)
                    except OSError:
                        pass  # http.client closed it at the end of the body; the next read returns b""
                chunk = raw.read1(min(65536, self.body_limit + 1 - length))
                if not chunk:
                    break
                chunks.append(chunk)
                length += len(chunk)
            if fired.is_set():
                raise FetchError("Request exceeded its total time limit")  # the body may be cut short
            body = b"".join(chunks)
            hdrs = {k.lower(): v for k, v in raw.getheaders()}
            if len(body) > self.body_limit:
                raise FetchError("Response body exceeds limit")
            if hdrs.get("content-encoding") == "gzip":
                with gzip.GzipFile(fileobj=io.BytesIO(body)) as stream:
                    body = stream.read(self.body_limit + 1)
                if len(body) > self.body_limit:
                    raise FetchError("Decompressed body exceeds limit")
            result = Response(url, url, raw.status, body, hdrs, source_class=source_class)
            receipt["status"] = raw.status
        except Exception as exc:
            message = str(exc)
            if fired.is_set():
                message = "Request exceeded its total time limit"
            elif isinstance(exc, FetchError) and not message.startswith("Budget:"):
                message = "Policy: " + message
            result = Response(url, url, error=message, source_class=source_class)
            if receipt is not None:
                receipt["error"] = message
        finally:
            if watchdog is not None:
                watchdog.cancel()
            if connection:
                connection.close()
            if receipt is not None:
                receipt["completed_at"] = now()
                # Keep completed attempts auditable even if the process is interrupted.
                # The final ordered ledger is still written by the batch runner.
                self.root.mkdir(parents=True, exist_ok=True)
                with self.host_lock("request_journal"):
                    with (self.root / "requests-live.jsonl").open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps(receipt, ensure_ascii=False) + "\n")
        return result

    TRANSIENT = ("timed out", "Connection reset", "Remote end closed", "EOF occurred", "Connection aborted", "RemoteDisconnected", "IncompleteRead")

    def _retry_wait(self, result, official, attempt):
        """Seconds to wait before retrying a transient failure, or None to stop."""
        if result.error:
            if result.error.startswith(("Budget:", "Robots:", "Cutoff:", "Replay:")) or not any(t in result.error for t in self.TRANSIENT):
                return None
            return 1.5 * (attempt + 1)
        if result.status == 429:
            try:
                wait = max(1.0, float(result.headers.get("retry-after", "2")))
            except ValueError:
                wait = 2.0
            if not official and wait > 5.0:
                return None  # A company site asking for a long pause is skipped, not waited out.
            return min(20.0, wait)
        if result.status in ({500, 502, 503, 504} if official else {502, 503, 504}):
            return 1.5 if attempt == 0 else 4.0
        return None

    def _robots_allowed(self, url, org):
        p = urllib.parse.urlsplit(url)
        origin = p.scheme + "://" + p.netloc
        with self.host_lock("robots:" + origin):
            if origin not in self.robots:
                # RFC 9309 2.3.1: follow up to five redirects (even across hosts; every hop is
                # re-validated as a public URL by _raw), 2xx = parse, 4xx = "unavailable" (allow
                # all), 429/5xx/network or TLS failure = "unreachable" (disallow all).
                target = origin + "/robots.txt"
                r = self._raw(target, org, "access_policy")
                if r.error and any(t in r.error for t in self.TRANSIENT) and time.monotonic() + 2 * self.timeout + 1 < self.budget.deadline:
                    # One patient retry: a slow or briefly overloaded small-business server is not
                    # "unreachable", and treating it so would withhold the whole site.
                    self._save(r)
                    time.sleep(1.0)
                    r = self._raw(target, org, "access_policy", timeout=int(self.timeout * 1.5))
                for _ in range(5):
                    if r.status not in {301, 302, 303, 307, 308} or not r.headers.get("location"):
                        break
                    self._save(r)
                    try:
                        target = safe_url(urllib.parse.urljoin(target, r.headers.get("location", "")))
                    except FetchError as exc:
                        r.error = "Robots: unsafe redirect " + str(exc)
                        break
                    r = self._raw(target, org, "access_policy")
                self._save(r)
                policy = Robots.from_response(None if r.error else r.status, r.body, error=bool(r.error))
                failure = (r.error or f"HTTP {r.status}") if policy.state == "unreachable" else None
                self.robots[origin] = (policy, r.snapshot_id, failure)
            policy, _, failure = self.robots[origin]
            if policy.state == "unreachable":
                return False, failure
            delay = policy.crawl_delay(ROBOTS_TOKEN)
            if delay and delay > 30:
                return False, None  # Honouring a very long crawl-delay would exceed the run budget: skip the site.
            return policy.allowed(ROBOTS_TOKEN, url), None

    def get(self, url, org, *, official=False, headers=None, cost=0.0, force=False, store=True):
        source_class = "official_registry" if official else "company_owned"
        initial = url
        try:
            url = safe_url(url)
            if self.replay:
                return self._replay(url)
            if self.registry_fetcher and official and re.fullmatch(r"https://data\.brreg\.no/enhetsregisteret/api/enheter/[0-9]{9}", url):
                return self.registry_fetcher._replay(url)
            if self.cutoff_instant and utc_timestamp(now()) > self.cutoff_instant:
                return Response(url, url, error="Cutoff: live evidence would be after the supplied cutoff", source_class=source_class)
            cached = None if force else self._cache_get(url)
            if cached is not None:
                return cached
            chain = []
            for _ in range(5):
                p = urllib.parse.urlsplit(url)
                if any(p.hostname == h or p.hostname.endswith("." + h) for h in RESTRICTED):
                    raise FetchError("Restricted platform; a licensed connector is required")
                is_official = official and p.hostname in OFFICIAL
                is_search = p.hostname == "api.search.brave.com" and bool(headers)
                if not is_official and not is_search:
                    allowed, failure = self._robots_allowed(url, org)
                    if not allowed:
                        # A robots.txt that could not be fetched means the site itself is unreachable
                        # (RFC 9309: assume disallow); that is a failure, not a refusal by the source.
                        error = ("Unreachable: robots.txt could not be fetched (" + str(failure)[:160] + ")") if failure else "Robots: access is disallowed by robots.txt"
                        return Response(initial, url, redirects=chain, error=error, source_class=source_class)
                result = self._raw(url, org, source_class, headers if not chain else None, cost if not chain else 0.0)
                for attempt in range(2 if is_official else 1):
                    wait = self._retry_wait(result, is_official, attempt)
                    if wait is None or time.monotonic() + wait + self.timeout > self.budget.deadline:
                        break
                    time.sleep(wait)
                    result = self._raw(url, org, source_class, headers if not chain else None, cost if not chain else 0.0)
                result.requested_url, result.redirects = initial, chain[:]
                result.policy = "official_open_api_NLOD_2.0" if is_official else "licensed_search_api" if is_search else "robots_checked_public_page"
                if store:  # Licensed search results are transient: never written to disk.
                    self._save(result)
                if result.status not in {301, 302, 303, 307, 308}:
                    self._cache_put(initial, result)
                    return result
                chain.append({"url": url, "status": result.status, "snapshot_id": result.snapshot_id})
                next_url = safe_url(urllib.parse.urljoin(url, result.headers.get("location", "")))
                if official and urllib.parse.urlsplit(next_url).hostname not in OFFICIAL:
                    raise FetchError("Official API redirected outside its trusted host")
                url = next_url
            raise FetchError("Too many redirects")
        except Exception as exc:
            return Response(initial, url, error="Policy: " + str(exc), source_class=source_class)

    def _replay(self, url):
        with self.host_lock("replay_index"):
            if not hasattr(self, "replay_index"):
                self.replay_index = {}
                for path in (self.replay / "snapshots").glob("ss_*.json"):
                    m = json.loads(path.read_text(encoding="utf-8"))
                    if self.cutoff_instant and utc_timestamp(m["retrieved_at"]) > self.cutoff_instant:
                        continue
                    for key in {safe_url(m["requested_url"]), safe_url(m["final_url"])}:
                        def rank(row):
                            return (row["retrieved_at"], row["http_status"] not in {301,302,303,307,308}, row["id"])
                        if key not in self.replay_index or rank(self.replay_index[key]) < rank(m):
                            self.replay_index[key] = m
        m = self.replay_index.get(url)
        if not m:
            return Response(url, url, error="Replay: no saved response for URL")
        body = read_snapshot_bytes(self.replay / m["storage_path"])
        if digest(body) != m["content_sha256"]:
            return Response(url, url, error="Replay: snapshot integrity failure")
        result = Response(m["requested_url"], m["final_url"], m["http_status"], body, m.get("headers", {}),
                          m["retrieved_at"], m["redirect_chain"], m.get("error"), m["source_class"],
                          m["storage_path"], m["access_policy"], True)
        if self.root.resolve() != self.replay.resolve():
            self._save(result)
        return result
