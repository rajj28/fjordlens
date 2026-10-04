"""Read-only local evidence workspace; no external service or API key needed."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import gzip
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from .synthesis import answer

def serve(data_path: Path, port=8787):
    text = gzip.decompress(data_path.read_bytes()).decode("utf-8") if data_path.suffix == ".gz" else data_path.read_text(encoding="utf-8")
    rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    profiles = {r["organisation_number"]: r for r in rows}
    static = Path(__file__).parent / "web"
    from .report import summary_row as summary  # One row format for the server and the static report.
    index = sorted((summary(p) for p in rows), key=lambda r: (-r["website_verified"], -r["claim_count"], r["name"]))
    class Handler(BaseHTTPRequestHandler):
        def send(self, body, content_type="application/json", status=200):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(body)
        def do_GET(self):
            p = urlsplit(self.path)
            params = parse_qs(p.query)
            if p.path == "/api/companies":
                self.send({"companies": index, "stats": {"profiles": len(rows), "claims": sum(len(p["claims"]) for p in rows),
                           "verified_sites": sum(r["website_verified"] for r in index),
                           "changes": sum(len(p["changes"]) for p in rows), "retrieved_at": max((r["updated_at"] for r in index), default=None)}})
            elif p.path.startswith("/api/company/"):
                record = profiles.get(p.path.rsplit("/", 1)[-1])
                self.send(record or {"error": "Company not found"}, status=200 if record else 404)
            elif p.path == "/api/ask":
                record = profiles.get(params.get("org", [""])[0])
                self.send(answer(record, params.get("q", [""])[0][:1000]) if record else {"error": "Company not found"}, status=200 if record else 404)
            elif p.path == "/api/export":
                self.send({"agent": "FjordLens", "profiles": rows})
            elif p.path in {"/", "/app.js", "/screen.js", "/style.css"}:
                name = "index.html" if p.path == "/" else p.path[1:]
                mime = {"index.html": "text/html", "app.js": "text/javascript", "screen.js": "text/javascript", "style.css": "text/css"}[name]
                self.send((static / name).read_bytes(), mime)
            else:
                self.send({"error": "Not found"}, status=404)
        def log_message(self, *args):
            pass
    print(f"FjordLens: http://127.0.0.1:{port} | {len(rows)} researched companies", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
