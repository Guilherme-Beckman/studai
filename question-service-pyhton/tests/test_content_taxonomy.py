import json
import tempfile
import unittest
from pathlib import Path

from studai.features.classification.taxonomy import (
    CONTENT_INDEX, DEFAULT_CONTENTS_BY_SUBJECT, DEFAULT_CONTENT_TAXONOMY, STAGE_ONE_OPTIONS,
    aggregate_content_ids, canonical_content_id, content_path, refinement_options,
    stage_one_id, validate_content_taxonomy,
)
from studai.integrations.llm import Provider
from studai.features.classification import service


class ContentTaxonomyTests(unittest.TestCase):
    def test_tree_has_thirteen_subjects_unique_nodes_sources_and_all_legacy_labels(self):
        self.assertEqual(len(DEFAULT_CONTENT_TAXONOMY["subjects"]), 13)
        self.assertEqual(len(CONTENT_INDEX), len(set(CONTENT_INDEX)))
        for subject, topics in service.LEGACY_TOPIC_TAXONOMY.items():
            actual = {node["name"] for node in CONTENT_INDEX.values()
                      if node["subject"] == subject and (node["is_leaf"] or node["refinable"])}
            self.assertTrue(set(topics).issubset(actual), subject)
        for node in CONTENT_INDEX.values():
            self.assertTrue(node["description"].strip())
            self.assertTrue(node["sources"], node["id"])

    def test_double_arc_is_a_leaf_under_trigonometric_identities_with_derived_source(self):
        content_id = "matematica.content.arco-duplo"
        self.assertEqual(content_path(content_id),
                         "Matemática → Trigonometria → Identidades e equações trigonométricas → Arco duplo")
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
        self.assertIn("matematica.content.identidades-e-equacoes-trigonometricas",
                      result["conteudos_agregados_ids"])
        with self.assertRaisesRegex(ValueError, "fora da taxonomia"):
            service.validate({"materia": "Física", "materias_secundarias": [],
                "conteudo_principal_id": primary, "conteudos_relacionados_ids": [], "confianca": 0.9},
                service.DEFAULT_TAXONOMY, content_taxonomy=DEFAULT_CONTENT_TAXONOMY)

    def test_deepened_subjects_have_only_subcontent_leaves_four_levels_deep(self):
        for subject in ("Matemática", "Física", "Química"):
            leaves = [node for node in CONTENT_INDEX.values()
                      if node["subject"] == subject and node["is_leaf"]]
            self.assertGreater(len(leaves), 150, subject)
            for node in leaves:
                self.assertEqual(node.get("level"), "subcontent", node["id"])
                self.assertEqual(len(node["path"]), 4, node["id"])
                self.assertEqual(stage_one_id(node["id"]), node["path_ids"][2])
            self.assertFalse(any(node["subject"] == subject and node["is_leaf"] for node in STAGE_ONE_OPTIONS))
        self.assertEqual(content_path("fisica.ondas-sonoras-e-nivel-de-intensidade.efeito-doppler"),
                         "Física → Ondas e óptica → Ondas sonoras e nível de intensidade → Efeito Doppler")

    def test_refinable_content_is_accepted_only_when_allowed(self):
        parent = "matematica.content.trigonometria-no-triangulo"
        self.assertIsNone(canonical_content_id(parent, subject="Matemática"))
        self.assertEqual(canonical_content_id(parent, subject="Matemática", allow_refinable=True), parent)
        self.assertIn("matematica.trigonometria-no-triangulo.lei-dos-senos",
                      [node["id"] for node in refinement_options(parent)])
        self.assertIn("matematica.area.trigonometria", aggregate_content_ids([parent]))

    def test_classifier_refines_content_into_subcontent_in_second_step(self):
        parent = "matematica.content.trigonometria-no-triangulo"
        leaf = "matematica.trigonometria-no-triangulo.lei-dos-senos"
        provider = ScriptedProvider([
            {"materia": "Matemática", "materias_secundarias": [], "conteudo_principal_id": parent,
             "conteudos_relacionados_ids": [], "confianca": 0.9},
            {"subconteudo_id": leaf, "confianca": 0.8}])
        question = {"numero": 1, "enunciado": "Num triângulo, sen A / a = ...", "alternativas": {}}
        system = service.build_system(service.DEFAULT_TAXONOMY)
        self.assertIn(f"[id={parent}]", system)
        self.assertNotIn(f"[id={leaf}]", system)
        result = service.classify_one(provider, system, question, None, service.DEFAULT_TAXONOMY, [])
        self.assertEqual(result["conteudo_principal_id"], leaf)
        self.assertEqual(result["conteudo_etapa1_id"], parent)
        self.assertEqual(result["caminho_conteudo"],
                         "Matemática → Trigonometria → Trigonometria no triângulo → Lei dos senos")
        self.assertEqual(result["confianca"], 0.8)
        self.assertIn("Lei dos senos", provider.systems[1])

    def test_classifier_keeps_content_and_flags_review_when_refinement_declines(self):
        parent = "matematica.content.trigonometria-no-triangulo"
        provider = ScriptedProvider([
            {"materia": "Matemática", "materias_secundarias": [], "conteudo_principal_id": parent,
             "conteudos_relacionados_ids": [], "confianca": 0.9},
            {"subconteudo_id": "REVISAR", "confianca": 0.2}])
        question = {"numero": 1, "enunciado": "Triângulo", "alternativas": {}}
        result = service.classify_one(provider, service.build_system(service.DEFAULT_TAXONOMY),
                                      question, None, service.DEFAULT_TAXONOMY, [])
        self.assertEqual(result["conteudo_principal_id"], parent)
        self.assertTrue(result["subconteudo_pendente"])
        self.assertEqual(result["confianca"], 0.5)
        from studai.features.questions.review import review_reasons
        self.assertIn("subconteudo_pendente", review_reasons({"classificacao": result}))

    def test_validator_rejects_duplicate_ids(self):
        sample = {"version": "test", "subjects": [{"id": "s", "name": "S", "description": "subject", "sources": [{"url": "x"}], "children": [
            {"id": "same", "name": "A", "description": "area", "sources": [{"url": "x"}], "children": [
                {"id": "leaf", "name": "X", "description": "x", "sources": [{"url": "x"}], "children": []}]},
            {"id": "same", "name": "B", "description": "area", "sources": [{"url": "x"}], "children": []}]}]}
        with self.assertRaisesRegex(ValueError, "duplicado"):
            validate_content_taxonomy(sample)


class ScriptedProvider(Provider):
    def __init__(self, answers):
        self.answers = list(answers)
        self.systems = []

    @property
    def label(self) -> str:
        return "scripted"

    def complete(self, system, user, images=None):
        self.systems.append(system)
        return json.dumps(self.answers.pop(0), ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
