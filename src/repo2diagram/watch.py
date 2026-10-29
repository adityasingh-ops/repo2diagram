"""`repo2diagram --watch`: serve the diagram on localhost and redraw it whenever code changes."""

from __future__ import annotations

import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from repo2diagram.scanner import list_files

RELOAD_SNIPPET = """<script>
(() => {
  let current = null;
  setInterval(async () => {
    try {
      const v = await (await fetch("/__version", {cache: "no-store"})).text();
      if (current === null) current = v;
      else if (v !== current) location.reload();
    } catch {}
  }, 1000);
})();
</script>
</body>"""


def snapshot(root: Path) -> dict[str, float]:
    out = {}
    for f in list_files(root):
        try:
            out[f] = (root / f).stat().st_mtime
        except OSError:
            pass
    return out


class LiveDiagram:
    """Holds the latest rendered page; ``build`` is called again on every change."""

    def __init__(self, build):
        self._build = build
        self.html = ""
        self.version = 0
        self.lock = threading.Lock()

    def refresh(self) -> None:
        html = self._build().replace("</body>", RELOAD_SNIPPET, 1)
        with self.lock:
            self.html = html
            self.version += 1


def make_handler(live: LiveDiagram):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:  # keep the terminal quiet
            pass

        def _send(self, body: str, ctype: str) -> None:
            data = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            with live.lock:
                html, version = live.html, live.version
            if self.path.startswith("/__version"):
                self._send(str(version), "text/plain")
            elif self.path in ("/", "/index.html"):
                self._send(html, "text/html; charset=utf-8")
            else:
                self.send_error(404)

    return Handler


def serve(root: Path, build, port: int = 8484, open_browser: bool = True, interval: float = 1.0,
          log=print) -> None:
    """Block forever: serve the diagram and rebuild it when files under ``root`` change."""
    live = LiveDiagram(build)
    live.refresh()
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(live))
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log(f"Watching {root} — live diagram at {url} (Ctrl+C to stop)")
    if open_browser:
        webbrowser.open(url)
    before = snapshot(root)
    try:
        while True:
            time.sleep(interval)
            now = snapshot(root)
            if now != before:
                changed = {f for f in now.keys() | before.keys() if now.get(f) != before.get(f)}
                before = now
                log(f"  ↻ {len(changed)} file(s) changed — redrawing")
                try:
                    live.refresh()
                except Exception as e:  # noqa: BLE001 - keep watching even if one build fails
                    log(f"  ! rebuild failed: {e}")
    except KeyboardInterrupt:
        log("stopped")
    finally:
        server.shutdown()
