import sqlite3
import sys
import tarfile
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from studai.tools.backup import create_backup, prune_backups


class BackupTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "root"
        self.dest = Path(self._tmp.name) / "backups"
        (self.root / "diagnostico").mkdir(parents=True)
        (self.root / "diagnostico" / "cursos.json").write_text("{}", encoding="utf-8")
        self.db_path = self.root / "diagnostico" / "historico.sqlite3"
        self.db = sqlite3.connect(self.db_path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE t (v TEXT)")
        self.db.execute("INSERT INTO t VALUES ('ok')")
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self._tmp.cleanup()

    def test_backup_includes_consistent_sqlite_and_skips_sidecars(self):
        archive = create_backup(self.root, ["diagnostico", "inexistente"], self.dest,
                                now=datetime(2026, 10, 28, 3, 0, 0))
        self.assertEqual(archive.name, "studai-20261028-030000.tar.gz")
        with tarfile.open(archive) as tar:
            names = tar.getnames()
            self.assertIn("diagnostico/cursos.json", names)
            self.assertIn("diagnostico/historico.sqlite3", names)
            self.assertFalse(any(n.endswith(("-wal", "-shm")) for n in names))
            tar.extract("diagnostico/historico.sqlite3", self.dest / "out")
        restored = sqlite3.connect(self.dest / "out" / "diagnostico" / "historico.sqlite3")
        try:
            self.assertEqual(restored.execute("SELECT v FROM t").fetchall(), [("ok",)])
        finally:
            restored.close()

    def test_prune_keeps_most_recent(self):
        for hour in range(4):
            create_backup(self.root, ["diagnostico"], self.dest, now=datetime(2026, 10, 28, hour))
        removed = prune_backups(self.dest, keep=2)
        self.assertEqual([p.name for p in removed],
                         ["studai-20261028-000000.tar.gz", "studai-20261028-010000.tar.gz"])
        self.assertEqual(sorted(p.name for p in self.dest.iterdir()),
                         ["studai-20261028-020000.tar.gz", "studai-20261028-030000.tar.gz"])


if __name__ == "__main__":
    unittest.main()
