import os
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer

from repo2diagram.watch import LiveDiagram, make_handler, snapshot


def test_live_diagram_serves_page_and_version(repo):
    pages = iter(["<html><body>one</body></html>", "<html><body>two</body></html>"])
    live = LiveDiagram(lambda: next(pages))
    live.refresh()
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(live))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        page = urllib.request.urlopen(base + "/").read().decode()
        assert "one" in page and "/__version" in page  # reload script injected
        assert urllib.request.urlopen(base + "/__version").read() == b"1"
        live.refresh()
        assert urllib.request.urlopen(base + "/__version").read() == b"2"
        assert "two" in urllib.request.urlopen(base + "/").read().decode()
    finally:
        server.shutdown()


def test_snapshot_notices_edits(repo):
    before = snapshot(repo)
    time.sleep(0.01)
    target = repo / "src/demo/db/models.py"
    target.write_text("class User: changed = True\n")
    os.utime(target, (before["src/demo/db/models.py"] + 5,) * 2)
    assert snapshot(repo) != before
