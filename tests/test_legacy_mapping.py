import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from studai.features.classification.legacy_mapping import (
    LegacyLabelOccurrence,
    ManualLabelMap,
    build_mapping_report,
    map_legacy_label,
    normalize_label,
)
from studai.features.classification.taxonomy import CONTENT_INDEX, canonical_content_id
from studai.tools import map_legacy_labels


def _leaf_with_unique_name():
    for node in CONTENT_INDEX.values():
        if node["is_leaf"] and canonical_content_id(node["name"], subject=node["subject"],
                                                    allow_refinable=True) == node["id"]:
            return node
    raise AssertionError("taxonomia sem folha de nome único")


def _leaf_with_alias():
    for node in CONTENT_INDEX.values():
        for alias in node.get("aliases", []):
            if (node["is_leaf"] and alias.casefold() != node["name"].casefold()
                    and canonical_content_id(alias, subject=node["subject"], allow_refinable=True) == node["id"]):
                return node, alias
    return None, None


class LegacyMappingTest(unittest.TestCase):
    def setUp(self):
        self.node = _leaf_with_unique_name()

    def test_normalize_label_folds_accents_case_and_punctuation(self):
        self.assertEqual(normalize_label("  Função, Afim — e QUADRÁTICA! "), "funcao afim e quadratica")

    def test_exact_match_by_name_and_id(self):
        by_name = map_legacy_label(self.node["subject"], self.node["name"])
        by_id = map_legacy_label(self.node["subject"], self.node["id"])
        self.assertEqual((by_name.kind, by_name.content_id), ("exact", self.node["id"]))
        self.assertEqual((by_id.kind, by_id.content_id), ("exact", self.node["id"]))

    def test_alias_match(self):
        node, alias = _leaf_with_alias()
        if node is None:
            self.skipTest("taxonomia atual sem aliases")
        match = map_legacy_label(node["subject"], alias)
        self.assertEqual((match.kind, match.content_id), ("alias", node["id"]))

    def test_normalized_match_requires_same_subject(self):
        mangled = normalize_label(self.node["name"]).upper().replace(" ", " - ")
        match = map_legacy_label(normalize_label(self.node["subject"]).upper(), mangled)
        self.assertEqual((match.kind, match.content_id), ("normalized", self.node["id"]))
        other_subject = next(node["subject"] for node in CONTENT_INDEX.values()
                             if node["subject"] != self.node["subject"])
        self.assertEqual(map_legacy_label(other_subject, mangled).kind, "unmatched")

    def test_no_fuzzy_guessing(self):
        match = map_legacy_label(self.node["subject"], self.node["name"] + " avançado")
        self.assertEqual((match.kind, match.content_id), ("unmatched", None))
        self.assertEqual(map_legacy_label(self.node["subject"], "REVISAR").kind, "unmatched")
        self.assertEqual(map_legacy_label("Astrologia", self.node["name"]).kind, "unmatched")

    def test_manual_override_and_invalid_target(self):
        manual = ManualLabelMap.from_payload({"mappings": {
            self.node["subject"]: {"Rótulo Antigo!": self.node["id"], "Outro": "id-inexistente"}}})
        match = map_legacy_label(self.node["subject"], "rotulo antigo", manual=manual)
        self.assertEqual((match.kind, match.content_id), ("manual", self.node["id"]))
        stale = map_legacy_label(self.node["subject"], "Outro", manual=manual)
        self.assertEqual(stale.kind, "unmatched")
        self.assertIn("id-inexistente", stale.reason)

    def test_shipped_manual_map_targets_exist(self):
        manual = ManualLabelMap.load()
        for (_subject, _topic), content_id in manual.entries.items():
            self.assertIn(content_id, CONTENT_INDEX)

    def test_report_counts_and_unmatched_list(self):
        report = build_mapping_report([
            LegacyLabelOccurrence(self.node["subject"], self.node["name"], "a"),
            LegacyLabelOccurrence(self.node["subject"], self.node["name"], "b", count=2),
            LegacyLabelOccurrence(self.node["subject"], "Tópico inventado", "a"),
        ])
        self.assertEqual(report["total_labels"], 2)
        self.assertEqual(report["total_occurrences"], 4)
        self.assertEqual(report["by_kind"]["exact"], {"labels": 1, "occurrences": 3})
        self.assertEqual([label["topic"] for label in report["unmatched"]], ["Tópico inventado"])


class MapLegacyLabelsCliTest(unittest.TestCase):
    def test_cli_reads_sources_and_does_not_rewrite_records(self):
        node = _leaf_with_unique_name()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog = root / "topic_taxonomy.json"
            catalog.write_text(json.dumps({node["subject"]: [node["name"], "Tópico sumido"]}), encoding="utf-8")
            cache = root / "cache"
            cache.mkdir()
            database = cache / "classification-cache.sqlite3"
            with sqlite3.connect(database) as connection:
                connection.execute("CREATE TABLE classifications (cache_key TEXT PRIMARY KEY, result_json TEXT)")
                connection.executemany("INSERT INTO classifications VALUES (?, ?)", [
                    ("k1", json.dumps({"materia": node["subject"], "assunto": "Tópico sumido"})),
                    ("k2", json.dumps({"materia": node["subject"], "assunto": node["name"],
                                       "conteudo_principal_id": node["id"]})),
                ])
            connection.close()
            jsonl = cache / "reviewed.jsonl"
            jsonl.write_text(json.dumps({"subject": node["subject"], "topic": node["name"]}) + "\n",
                             encoding="utf-8")
            questions = root / "processados" / "prova"
            questions.mkdir(parents=True)
            (questions / "questoes.classificado.json").write_text(json.dumps({"questoes": [
                {"numero": 1, "classificacao": {"materia": node["subject"], "assunto": "Tópico sumido"}}]}),
                encoding="utf-8")
            before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
            output = root / "docs" / "legacy-label-mapping.md"

            report = map_legacy_labels.run(catalog=catalog, cache_dir=cache, questions_dir=root / "processados",
                                           manual_map=None, output=output)

            self.assertEqual(report["skipped"], {"cache:classification-cache.sqlite3": 1})
            self.assertEqual(report["by_kind"]["exact"]["occurrences"], 2)
            self.assertEqual(report["unmatched"][0]["topic"], "Tópico sumido")
            self.assertEqual(report["unmatched"][0]["occurrences"], 3)
            self.assertIn("Tópico sumido", output.read_text(encoding="utf-8"))
            self.assertEqual(json.loads(output.with_suffix(".json").read_text(encoding="utf-8"))["total_labels"], 2)
            for path, content in before.items():
                self.assertEqual(path.read_bytes(), content, f"registro alterado: {path}")


if __name__ == "__main__":
    unittest.main()
