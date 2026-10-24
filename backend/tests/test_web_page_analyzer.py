import threading
import http.server
import socketserver

import pytest

from backend.app.services import web_page_analyzer as w


PAGES = {
    "/": "<html><head><title>Acme Home</title></head><body><p>We make custom notebooks.</p><a href='/about'>About</a><a href='/products'>Products</a><a href='/contact'>Contact</a></body></html>",
    "/about": "<html><head><title>About Acme</title></head><body><p>Founded in 2008 in Ningbo.</p><a href='/products'>Products</a></body></html>",
    "/products": "<html><head><title>Acme Products</title></head><body><p>Composition notebooks and journals.</p><a href='/products/detail'>Detail</a></body></html>",
    "/products/detail": "<html><head><title>Notebook A5</title></head><body><p>80 pages, custom cover, MOQ 500.</p></body></html>",
    "/contact": "<html><head><title>Contact</title></head><body><p>Email: sales@acme.example Phone: +86 574 8888 6666</p></body></html>",
}


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = PAGES.get(self.path.split("?")[0])
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture()
def local_site(monkeypatch):
    server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(w, "validate_public_url", lambda url: url)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()


def test_ssrf_blocks_private_and_file():
    for url in ("http://127.0.0.1:8000", "http://169.254.169.254/latest/meta-data", "file:///etc/passwd"):
        with pytest.raises(w.WebFetchError):
            w.validate_public_url(url)


def test_analyze_single_page(local_site):
    page = w.analyze_page(f"{local_site}/about")
    assert page["status"] == "ok"
    assert "Acme" in page["title"]
    assert page["word_count"] > 0


def test_crawl_multi_level(local_site):
    result = w.crawl_site(
        [f"{local_site}/"],
        max_depth=2,
        max_pages=6,
        per_host_delay_ms=0,
        time_budget_seconds=30,
    )
    pages = result["sites"][0]["pages"]
    assert len(pages) >= 4
    assert any(page["depth"] == 2 for page in pages)
    contacts = result["sites"][0]["contacts"]
    assert "sales@acme.example" in contacts["emails"]
    assert contacts["phones"]