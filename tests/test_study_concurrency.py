"""Sincronizações simultâneas (várias rotas ao abrir o Treinamento) não podem falhar."""
import tempfile
import threading
import unittest
from pathlib import Path

from studai.features.diagnostics import service as diagnostics
from studai.features.study import service as study
from test_study_scheduler import entry, leaves


class ConcurrentSyncTests(unittest.TestCase):
    def test_parallel_syncs_import_each_answer_once(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "h.sqlite3"
            db = study.connect_db(path)
            math = leaves("Matemática", 6)
            pool = [entry("prova-1", n + 1, "Matemática", leaf) for n, leaf in enumerate(math)]
            config = {"version": 1, "cursos": {"curso": {"nome": "x", "pesos": {"Matemática": 1}}}}
            attempt = diagnostics.create_attempt(db, config, "curso", pool, total=6, seed=1, provider=None,
                                                 mode="static")
            for ordinal in range(1, 7):
                diagnostics.record_response(db, attempt["id"], ordinal, "B", "calc_error")
            db.close()
            barrier, errors, counts = threading.Barrier(4), [], []

            def run():
                connection = study.connect_db(path)
                try:
                    barrier.wait()
                    counts.append(study.sync_diagnostic_answers(connection))
                except Exception as error:  # noqa: BLE001 - o teste registra qualquer falha
                    errors.append(error)
                finally:
                    connection.close()

            threads = [threading.Thread(target=run) for _ in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(errors, [])
            self.assertEqual(sum(counts), 6)
            db = study.connect_db(path)
            try:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM review_logs").fetchone()[0], 12)
            finally:
                db.close()


    def test_parallel_first_opening_of_the_day_shares_one_session(self):
        from datetime import datetime, timezone
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "h.sqlite3"
            study.connect_db(path).close()
            pool = [entry("prova-1", n + 1, "Matemática", leaf) for n, leaf in enumerate(leaves("Matemática", 4))]
            config = {"version": 1, "cursos": {"curso": {"nome": "x", "pesos": {"Matemática": 1}}}}
            now = datetime(2026, 10, 22, 15, tzinfo=timezone.utc)
            barrier, errors, ids = threading.Barrier(4), [], []

            def run():
                connection = study.connect_db(path)
                try:
                    barrier.wait()
                    ids.append(study.plan_today(connection, pool, config, "curso", now)["id"])
                except Exception as error:  # noqa: BLE001
                    errors.append(error)
                finally:
                    connection.close()

            threads = [threading.Thread(target=run) for _ in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(errors, [])
            self.assertEqual(len(set(ids)), 1)


if __name__ == "__main__":
    unittest.main()
