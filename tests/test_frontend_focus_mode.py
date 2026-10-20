import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from studai.api import server


class FocusModeStaticTests(unittest.TestCase):
    """O componente do modo foco (frontend/focus-mode.js) precisa ser servido pela API local."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        web = Path(self.temp.name)
        (web / "focus-mode.js").write_text("window.studaiFocus = {};\n", encoding="utf-8")
        self.patch = patch.object(server, "WEB_ROOT", web)
        self.patch.start()
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(timeout=2)
        self.patch.stop()
        self.temp.cleanup()

    def test_focus_mode_js_is_served_as_javascript(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.http.server_port}/focus-mode.js") as response:
            self.assertIn("javascript", response.headers["Content-Type"])
            self.assertIn(b"studaiFocus", response.read())

    def test_index_loads_focus_mode_before_app(self):
        index = (Path(__file__).resolve().parents[1] / "frontend" / "index.html").read_text(encoding="utf-8")
        self.assertLess(index.index('src="/focus-mode.js"'), index.index('src="/app.js"'))


if __name__ == "__main__":
    unittest.main()
