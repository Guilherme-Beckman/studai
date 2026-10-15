import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import Mock

from studai.features.classification.topic_embeddings import TopicEmbeddingIndex


class TopicEmbeddingTests(unittest.TestCase):
    def test_cache_reuses_vectors_and_invalidates_for_model_or_taxonomy_change(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "topic-vectors.json"
            embed = Mock(side_effect=lambda texts, **_: [[float(index + 1), 1.0]
                                                        for index, _text in enumerate(texts)])
            taxonomy = {"Matemática": ["Probabilidade", "Geometria"]}
            index = TopicEmbeddingIndex(path, model="embed-a", embed=embed)
            first = index.build(taxonomy, batch_size=1)
            self.assertFalse(first["cached"])
            self.assertEqual(embed.call_count, 2)
            self.assertTrue(index.build(taxonomy)["cached"])
            self.assertEqual(embed.call_count, 2)

            changed_taxonomy = {"Matemática": ["Probabilidade", "Geometria", "Funções"]}
            self.assertFalse(index.build(changed_taxonomy)["cached"])
            self.assertEqual(embed.call_count, 3)
            changed_model = TopicEmbeddingIndex(path, model="embed-b", embed=embed)
            self.assertFalse(changed_model.build(changed_taxonomy)["cached"])
            self.assertEqual(embed.call_count, 4)

    def test_rank_returns_highest_cosine_similarity_first(self):
        with tempfile.TemporaryDirectory() as temp:
            index = TopicEmbeddingIndex(Path(temp) / "topic-vectors.json", model="embed",
                                        embed=lambda texts, **_: [[1.0, 0.0], [0.0, 1.0]])
            taxonomy = {"Física": ["Mecânica", "Óptica"]}
            index.build(taxonomy)
            self.assertEqual(index.rank([0.9, 0.1], limit=2, topics_by_subject=taxonomy)[0]["topic"],
                             "Mecânica")

    def test_stage_one_index_ranks_inside_subject_and_resolves_legacy_labels(self):
        from studai.features.classification.taxonomy import DEFAULT_CONTENT_TAXONOMY, STAGE_ONE_OPTIONS
        from studai.features.classification.topic_embeddings import taxonomy_rows
        from studai.tools.benchmark_topic_retrieval import measure_by_subject

        rows = taxonomy_rows(DEFAULT_CONTENT_TAXONOMY, "stage1")
        self.assertEqual(len(rows), len(STAGE_ONE_OPTIONS))
        senos = next(row for row in rows if row["content_id"] == "matematica.content.trigonometria-no-triangulo")
        self.assertIn("Lei dos senos", senos["aliases"])

        def embed(texts, **_):
            return [[1.0 if "senos" in text.casefold() else 0.0, 0.1] for text in texts]

        with tempfile.TemporaryDirectory() as temp:
            dataset = Path(temp) / "reviewed.jsonl"
            dataset.write_text(json.dumps({"id": "q1", "subject": "Matemática",
                                           "topic": "Trigonometria no triângulo",
                                           "question": "Use a lei dos senos no triângulo."}) + "\n",
                               encoding="utf-8")
            report = measure_by_subject(dataset, base_url="http://x", model="fake",
                                        cache_path=Path(temp) / "stage1.json",
                                        output=Path(temp) / "out.json",
                                        report_path=Path(temp) / "out.md", embed=embed)
            item = report["items"][0]
            self.assertEqual(item["stage1_id"], "matematica.content.trigonometria-no-triangulo")
            self.assertEqual(item["gold_rank"], 1)
            self.assertEqual(item["subject_options"],
                             sum(node["subject"] == "Matemática" for node in STAGE_ONE_OPTIONS))
            self.assertTrue(all(c["subject"] == "Matemática" for c in item["top_candidates"]))
            self.assertEqual(report["recall_at_k"]["3"]["recall"], 1.0)
            self.assertIn("| Matemática | 1 |", (Path(temp) / "out.md").read_text(encoding="utf-8"))

    def test_cache_rebuilds_when_topic_order_no_longer_matches_vectors(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "topic-vectors.json"
            embed = Mock(return_value=[[1.0, 0.0], [0.0, 1.0]])
            taxonomy = {"Física": ["Mecânica", "Óptica"]}
            index = TopicEmbeddingIndex(path, model="embed", embed=embed)
            index.build(taxonomy)
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["topics"].reverse()
            path.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "ausente ou incompatível"):
                index.rank([1.0, 0.0], topics_by_subject=taxonomy)
            result = index.build(taxonomy)
            self.assertFalse(result["cached"])
            self.assertEqual(embed.call_count, 2)


if __name__ == "__main__":
    unittest.main()
