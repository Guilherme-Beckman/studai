import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from _reports_support import CONTENTS, COURSE_ID, build, config, load_pool
from studai.api import server
from studai.features.classification.taxonomy import CONTENT_INDEX
from studai.features.diagnostics import depth
from studai.features.study import service as study
from studai.tools.simulate_student import simulate

LEAF = CONTENTS["Matemática"][0]


class KnowledgeMapApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.processed, config_path, self.db_path = build(root)
        web = root / "web"
        web.mkdir()
        (web / "knowledge-map.js").write_text("export const ok = true;\n", encoding="utf-8")
        db = study.connect_db(self.db_path)
        try:
            self.pool = load_pool(db, self.processed)
            simulate(db, config(), COURSE_ID, self.pool, sessions=2, exams=1, seed=5,
                     end=date(2026, 10, 19), per_diagnostic=10)
        finally:
            db.close()
        self.patches = [
            patch.object(server, "PROCESSED_ROOT", self.processed),
            patch.object(server, "DEFAULT_QUESTIONS_DIR", self.processed),
            patch.object(server, "DEFAULT_DIAGNOSTICS_DB", self.db_path),
            patch.object(server, "DEFAULT_COURSE_CONFIG", config_path),
            patch.object(server, "WEB_ROOT", web),
            patch.dict(server.POOL_CACHE, clear=True),
        ]
        for active in self.patches:
            active.start()
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.http.server_port}"

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(timeout=2)
        for active in reversed(self.patches):
            active.stop()
        self.temp.cleanup()

    def request(self, path):
        try:
            with urllib.request.urlopen(self.base + path) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            with error:
                return error.code, json.loads(error.read())

    def study_leaf(self):
        """Acerta no "Hoje" uma questão do subconteúdo ``LEAF``."""
        db = study.connect_db(self.db_path)
        try:
            pool = [item for item in self.pool if study.content_id_of(item["question"]) == LEAF]
            now = datetime(2026, 10, 20, 13, tzinfo=timezone.utc)
            session = study.plan_today(db, pool, config(), COURSE_ID, now, new_per_day=1, size=1)
            item = session["items"][0]
            key = next(e["correct_answer"] for e in pool if (e["source_id"], e["number"]) == (item["sourceId"], item["number"]))
            return study.record_study_answer(db, session["id"], item["id"], key, None, now, pool)
        finally:
            db.close()

    def test_graph_covers_the_live_tree_with_edges(self):
        status, payload = self.request("/api/knowledge-map")
        self.assertEqual(status, 200)
        self.assertEqual(payload["course"]["id"], COURSE_ID)
        nodes = {node["id"]: node for node in payload["nodes"]}
        live = {identity for identity, node in CONTENT_INDEX.items()
                if node["subject"] in ("Matemática", "Física") and identity in depth.LIVE_CHILDREN}
        self.assertTrue(live <= set(nodes))
        self.assertEqual(set(nodes) - live, {"matematica", "fisica"})
        self.assertEqual(len(payload["edges"]), len(nodes) - 2)
        for edge in payload["edges"]:
            self.assertEqual(nodes[edge["target"]]["parent_id"], edge["source"])
        self.assertGreater(nodes["matematica"]["depth"], 0)
        self.assertEqual(nodes[LEAF]["questions"], 8)  # 4 por prova, nas duas provas
        self.assertEqual(payload["kpis"]["nodes"], len(nodes))
        self.assertGreater(payload["kpis"]["practiced"], 0)
        status, _ = self.request("/api/knowledge-map?courseId=nao-existe")
        self.assertEqual(status, 404)

    def test_daily_answers_feed_the_map(self):
        _, before = self.request(f"/api/knowledge-map?courseId={COURSE_ID}")
        self.study_leaf()
        _, after = self.request(f"/api/knowledge-map?courseId={COURSE_ID}")
        attempts = lambda payload: next(n for n in payload["nodes"] if n["id"] == LEAF)["attempts"]
        self.assertEqual(attempts(after), attempts(before) + 1)
        status, detail = self.request(f"/api/knowledge-map/nodes/{LEAF}?courseId={COURSE_ID}")
        self.assertEqual(status, 200)
        self.assertEqual(detail["recent_answers"][-1]["kind"], "daily")

    def test_node_detail_lists_children_and_questions(self):
        content = CONTENT_INDEX[LEAF]["path_ids"][2]
        status, detail = self.request(f"/api/knowledge-map/nodes/{content}?courseId={COURSE_ID}")
        self.assertEqual(status, 200)
        self.assertEqual(detail["node"]["id"], content)
        self.assertEqual(detail["ancestors"], CONTENT_INDEX[content]["path_ids"][:-1])
        self.assertEqual({child["id"] for child in detail["children"]}, set(depth.LIVE_CHILDREN[content]))
        self.assertTrue(detail["questions"])
        self.assertLessEqual(len(detail["questions"]), 10)
        self.assertTrue(all(q["content_path"].startswith("Matemática") for q in detail["questions"]))
        status, error = self.request(f"/api/knowledge-map/nodes/matematica.nao-existe?courseId={COURSE_ID}")
        self.assertEqual(status, 404)
        self.assertIn("error", error)

    def test_script_is_served_as_javascript(self):
        with urllib.request.urlopen(self.base + "/knowledge-map.js") as response:
            self.assertIn("javascript", response.headers["Content-Type"])


if __name__ == "__main__":
    unittest.main()
