"""Android entry point: start the existing Python HTTP server on localhost."""
from __future__ import annotations

import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

from server import App, make_handler

_server = None


def start(files_dir: str) -> int:
    """Start the loopback server using the bundled vocabulary."""
    global _server
    base = Path(__file__).resolve().parent
    data = Path(files_dir) / "data"
    data.mkdir(parents=True, exist_ok=True)
    seed = base / "android_assets" / "data" / "words.json"
    web = base / "android_assets" / "web"
    app = App(data, web, builtin_words=seed if seed.is_file() else None)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    _server = httpd
    return int(httpd.server_address[1])
