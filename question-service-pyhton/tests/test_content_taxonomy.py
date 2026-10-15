import json
import tempfile
import unittest
from pathlib import Path

from studai.features.classification.taxonomy import (
    CONTENT_INDEX, DEFAULT_CONTENTS_BY_SUBJECT, DEFAULT_CONTENT_TAXONOMY,
    aggregate_content_ids, canonical_content_id, content_path,
    validate_content_taxonomy,
)
from studai.features.classification import service


class ContentTaxonomyTests(unittest.TestCase):
    def test_tree_has_thirteen_subjects_unique_nodes_sources_and_all_legacy_labels(self):
        self.assertEqual(len(DEFAULT_CONTENT_TAXONOMY["subjects"]), 13)
        self.assertEqual(len(CONTENT_INDEX), len(set(CONTENT_INDEX)))
        for subject, topics in service.LEGACY_TOPIC_TAXONOMY.items():
            actual = {node["name"] for node in DEFAULT_CONTENTS_BY_SUBJECT[subject]}
            self.assertTrue(set(topics).issubset(actual), subject)
        for node in CONTENT_INDEX.values():
            self.assertTrue(node["description"].strip())
            self.assertTrue(node["sources"], node["id"])

    def test_double_arc_is_a_leaf_under_trigonometric_identities_with_derived_source(self):
        content_id = "matematica.content.arco-duplo"
        self.assertEqual(content_path(content_id),
                         "Matemática → Trigonometria → Identidades trigonométricas → Arco duplo")
        node = CONTENT_INDEX[content_id]
        self.assertTrue(node["editorial_detail"])
        self.assertIn("Fórmula do ângulo duplo", node["aliases"])
        self.assertEqual(canonical_content_id("Fórmula do ângulo duplo", subject="Matemática"), content_id)

    def test_classification_uses_primary_and_related_canonical_leaf_ids(self):
        primary = "matematica.content.arco-duplo"
        related = DEFAULT_CONTENTS_BY_SUBJECT["Matemática"][0]["id"]
        result = service.validate({"materia": "Matemática", "materias_secundarias": [],
            "conteudo_principal_id": primary, "conteudos_relacionados_ids": [related], "confianca": 0.91},
            service.DEFAULT_TAXONOMY, content_taxonomy=DEFAULT_CONTENT_TAXONOMY)
        self.assertEqual(result["conteudo_principal_id"], primary)
        self.assertEqual(result["conteudos_relacionados_ids"], [related])
        self.assertIn("matematica.trigonometria.identidades", result["conteudos_agregados_ids"])
        with self.assertRaisesRegex(ValueError, "fora da taxonomia"):
            service.validate({"materia": "Física", "materias_secundarias": [],
                "conteudo_principal_id": primary, "conteudos_relacionados_ids": [], "confianca": 0.9},
                service.DEFAULT_TAXONOMY, content_taxonomy=DEFAULT_CONTENT_TAXONOMY)

    def test_validator_rejects_duplicate_ids(self):
        sample = {"version": "test", "subjects": [{"id": "s", "name": "S", "description": "subject", "sources": [{"url": "x"}], "children": [
            {"id": "same", "name": "A", "description": "area", "sources": [{"url": "x"}], "children": [
                {"id": "leaf", "name": "X", "description": "x", "sources": [{"url": "x"}], "children": []}]},
            {"id": "same", "name": "B", "description": "area", "sources": [{"url": "x"}], "children": []}]}]}
        with self.assertRaisesRegex(ValueError, "duplicado"):
            validate_content_taxonomy(sample)


if __name__ == "__main__":
    unittest.main()
