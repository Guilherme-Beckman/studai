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

    def test_every_leaf_is_a_subcontent_four_levels_deep(self):
        leaves = [node for node in CONTENT_INDEX.values() if node["is_leaf"]]
        self.assertGreater(len(leaves), 1400)
        for node in leaves:
            self.assertEqual(node.get("level"), "subcontent", node["id"])
            self.assertEqual(len(node["path"]), 4, node["id"])
            self.assertEqual(stage_one_id(node["id"]), node["path_ids"][2])
        self.assertTrue(all(node["refinable"] for node in STAGE_ONE_OPTIONS))
        self.assertEqual(len({node["subject"] for node in leaves}), 13)
        self.assertEqual(content_path("fisica.ondas-sonoras-e-nivel-de-intensidade.efeito-doppler"),
                         "Física → Ondas e óptica → Ondas sonoras e nível de intensidade → Efeito Doppler")
        self.assertEqual(content_path("historia.revolucao-francesa.era-napoleonica"),
                         "História → Revoluções e transformações econômicas → Revolução Francesa → Era napoleônica")

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

    def test_routed_classifier_uses_short_per_subject_prompts(self):
        parent = "matematica.content.trigonometria-no-triangulo"
        leaf = "matematica.trigonometria-no-triangulo.lei-dos-senos"
        provider = ScriptedProvider([
            {"materia": "Matemática", "materias_secundarias": ["Física"], "confianca": 0.95},
            {"conteudo_principal_id": parent, "conteudos_relacionados_ids": [], "confianca": 0.8},
            {"subconteudo_id": leaf, "confianca": 0.7}])
        question = {"numero": 1, "enunciado": "Num triângulo, sen A / a = ...", "alternativas": {}}
        result = service.classify_routed(provider, question, None, service.DEFAULT_TAXONOMY, [])
        self.assertEqual(result["materia"], "Matemática")
        self.assertEqual(result["materias_secundarias"], ["Física"])
        self.assertEqual(result["conteudo_principal_id"], leaf)
        self.assertEqual(result["confianca"], 0.7)
        self.assertEqual(result["roteamento"], "hierarchical")
        subject_system, content_system, refine_system = provider.systems
        self.assertNotIn("[id=", subject_system)
        self.assertIn(f"[id={parent}]", content_system)
        self.assertNotIn("[id=fisica.", content_system)
        self.assertIn("Lei dos senos", refine_system)
        flat_system = service.build_system(service.DEFAULT_TAXONOMY)
        self.assertLess(len(subject_system) + len(content_system), len(flat_system) / 4)

    def test_routed_classifier_falls_back_to_secondary_subject(self):
        content = "fisica.content.movimento-harmonico-simples"
        provider = ScriptedProvider([
            {"materia": "Matemática", "materias_secundarias": ["Física"], "confianca": 0.6},
            {"conteudo_principal_id": "REVISAR", "conteudos_relacionados_ids": [], "confianca": 0.2},
            {"conteudo_principal_id": content, "conteudos_relacionados_ids": [], "confianca": 0.8},
            {"subconteudo_id": "fisica.movimento-harmonico-simples.pendulo-simples", "confianca": 0.9}])
        question = {"numero": 2, "enunciado": "Período de um pêndulo", "alternativas": {}}
        result = service.classify_routed(provider, question, None, service.DEFAULT_TAXONOMY, [])
        self.assertEqual(result["materia"], "Física")
        self.assertEqual(result["materia_reroteada_de"], "Matemática")
        self.assertEqual(result["conteudo_principal_id"],
                         "fisica.movimento-harmonico-simples.pendulo-simples")
        self.assertIn("Física", provider.systems[2])

    def test_routed_classifier_retries_invalid_content_for_subject(self):
        provider = ScriptedProvider([
            {"materia": "Química", "materias_secundarias": [], "confianca": 0.9},
            {"conteudo_principal_id": "matematica.content.probabilidade", "conteudos_relacionados_ids": [],
             "confianca": 0.9},
            {"conteudo_principal_id": "REVISAR", "conteudos_relacionados_ids": [], "confianca": 0.1}])
        question = {"numero": 3, "enunciado": "Reação", "alternativas": {}}
        result = service.classify_routed(provider, question, None, service.DEFAULT_TAXONOMY, [])
        self.assertIsNone(result["conteudo_principal_id"])
        self.assertEqual(result["assunto"], "REVISAR")
        self.assertEqual(result["tentativas"], 3)

    def test_batch_routing_classifies_many_questions_with_one_request_per_stage_group(self):
        provider = BatchProvider()
        entries = [(str(n), service.render_questao({"enunciado": text, "alternativas": {}}, None, False))
                   for n, text in ((1, "triângulo lei dos senos"), (2, "triângulo área"), (3, "pêndulo"))]
        results = service.classify_batch_routed(provider, entries, service.DEFAULT_TAXONOMY)
        self.assertEqual(set(results), {"1", "2", "3"})
        self.assertEqual(results["1"]["conteudo_principal_id"],
                         "matematica.trigonometria-no-triangulo.lei-dos-senos")
        self.assertEqual(results["3"]["materia"], "Física")
        self.assertEqual(results["3"]["roteamento"], "hierarchical-batch")
        # 1 matéria + 2 conteúdo (Matemática, Física) + 3 subconteúdo (um por conteúdo pai),
        # contra 9 requests questão a questão.
        self.assertEqual(len(provider.calls), 6)
        self.assertTrue(all("MODO LOTE" in system for system, _ in provider.calls))

    def test_batch_items_missing_from_answer_fall_back_to_single_classification(self):
        import tempfile
        from pathlib import Path
        provider = BatchProvider(drop_ids={"2"})
        with tempfile.TemporaryDirectory() as temp:
            exam = Path(temp) / "questoes.json"
            exam.write_text(json.dumps({"prova": "t", "questoes": [
                {"numero": 1, "enunciado": "triângulo lei dos senos", "alternativas": {}},
                {"numero": 2, "enunciado": "triângulo área", "alternativas": {}}]}), encoding="utf-8")
            data = service.classificar(exam, provider, service.DEFAULT_TAXONOMY, workers=1,
                                       use_cache=False, use_rag=False, batch_size=5, log=lambda *_: None)
        by_number = {q["numero"]: q["classificacao"] for q in data["questoes"]}
        self.assertEqual(by_number[1]["roteamento"], "hierarchical-batch")
        self.assertEqual(by_number[2]["roteamento"], "hierarchical")
        self.assertEqual(by_number[2]["conteudo_principal_id"],
                         "matematica.areas-de-figuras-planas.areas-de-poligonos")

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


class BatchProvider(Provider):
    """Answers the routed prompts by keyword, in batch or single mode."""
    SUBJECT = {"senos": ("Matemática", "matematica.content.trigonometria-no-triangulo",
                         "matematica.trigonometria-no-triangulo.lei-dos-senos"),
               "área": ("Matemática", "matematica.content.areas-de-figuras-planas",
                        "matematica.areas-de-figuras-planas.areas-de-poligonos"),
               "pêndulo": ("Física", "fisica.content.movimento-harmonico-simples",
                           "fisica.movimento-harmonico-simples.pendulo-simples")}

    def __init__(self, drop_ids=()):
        super().__init__(model="batch")
        self.calls = []
        self.drop_ids = set(drop_ids)

    def _label(self, text):
        return next(value for key, value in self.SUBJECT.items() if key in text)

    def _answer(self, system, text):
        subject, content, leaf = self._label(text)
        if "identifica a matéria" in system:
            return {"materia": subject, "materias_secundarias": [], "confianca": 0.9}
        if "classifica o conteúdo" in system:
            return {"conteudo_principal_id": content, "conteudos_relacionados_ids": [], "confianca": 0.8}
        return {"subconteudo_id": leaf, "confianca": 0.7}

    def complete(self, system, user, images=None):
        self.calls.append((system, user))
        if "MODO LOTE" not in system:
            return json.dumps(self._answer(system, user), ensure_ascii=False)
        import re
        items = []
        for key, body in re.findall(r'<questao id="([^"]+)">(.*?)</questao>', user, flags=re.S):
            if key not in self.drop_ids:
                items.append({"id": key, **self._answer(system, body)})
        return json.dumps({"itens": items}, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
