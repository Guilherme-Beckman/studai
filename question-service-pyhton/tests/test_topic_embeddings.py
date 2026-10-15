import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
