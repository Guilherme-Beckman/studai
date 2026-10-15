import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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

    def test_question_first_layout_shares_system_and_question_prefix_across_stages(self):
        parent = "matematica.content.trigonometria-no-triangulo"
        leaf = "matematica.trigonometria-no-triangulo.lei-dos-senos"
        provider = ScriptedProvider([
            {"materia": "Matemática", "materias_secundarias": [], "confianca": 0.95},
            {"conteudo_principal_id": parent, "conteudos_relacionados_ids": [], "confianca": 0.8},
            {"subconteudo_id": leaf, "confianca": 0.7}])
        question = {"numero": 1, "enunciado": "Num triângulo, sen A / a = ...", "alternativas": {}}
        examples = [{"text": "Exemplo de trigonometria",
                     "result": {"subject": "Matemática", "content_id": parent}}]
        with mock.patch.object(service, "QUESTION_FIRST", True):
            result = service.classify_routed(provider, question, None, service.DEFAULT_TAXONOMY, [], examples)
        self.assertEqual(result["conteudo_principal_id"], leaf)
        self.assertEqual(set(provider.systems), {service.SHARED_STAGE_SYSTEM})
        prefix = service.render_questao(question, None, False) + "\n\n" + service.render_examples(examples)
        subject_user, content_user, refine_user = provider.users
        for user in provider.users:
            self.assertTrue(user.startswith(prefix + "\n\n<etapa>\n"))
            self.assertTrue(user.endswith("</etapa>"))
        self.assertIn("identifica a matéria", subject_user)
        self.assertIn(f"[id={parent}]", content_user)
        self.assertIn("Lei dos senos", refine_user)
        self.assertNotIn("Lei dos senos", content_user)

    def test_question_first_retry_keeps_question_prefix(self):
        provider = ScriptedProvider([
            {"materia": "Astrologia", "materias_secundarias": [], "confianca": 0.9},
            {"materia": "Física", "materias_secundarias": [], "confianca": 0.9},
            {"conteudo_principal_id": "REVISAR", "conteudos_relacionados_ids": [], "confianca": 0.2}])
        question = {"numero": 2, "enunciado": "Um pêndulo oscila...", "alternativas": {}}
        with mock.patch.object(service, "QUESTION_FIRST", True):
            service.classify_routed(provider, question, None, service.DEFAULT_TAXONOMY, [])
        first, retry, _ = provider.users
        self.assertTrue(retry.startswith(first))
        self.assertIn("resposta anterior foi inválida", retry[len(first):])

    def test_question_first_works_with_demo_provider(self):
        from studai.integrations.llm import DemoProvider
        question = {"numero": 3, "enunciado": "Calcule a probabilidade de um triângulo...", "alternativas": {}}
        with mock.patch.object(service, "QUESTION_FIRST", True):
            result = service.classify_routed(DemoProvider(model="demo"), question, None,
                                             service.DEFAULT_TAXONOMY, [])
        self.assertEqual(result["materia"], "Matemática")
        self.assertTrue(result["conteudo_principal_id"].startswith("matematica."))

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

    def test_stage_major_routing_runs_each_stage_for_all_questions_grouped_by_prompt(self):
        provider = StageProvider()
        texts = {1: "triângulo lei dos senos", 2: "pêndulo", 3: "triângulo área",
                 4: "massa que oscila", 5: "outro senos"}
        items = [{"key": n, "question": {"numero": n, "enunciado": text, "alternativas": {}}}
                 for n, text in texts.items()]
        finished = []
        results = service.classify_stage_major(
            provider, items, service.DEFAULT_TAXONOMY,
            on_done=lambda key, result, usages, seconds: finished.append(key))
        self.assertEqual(provider.order(), [
            *[("materia", None)] * 5,
            # conteúdo agrupado pela matéria escolhida: Matemática (1, 3, 4, 5) e depois Física (2)
            ("conteudo", "Matemática"), ("conteudo", "Matemática"), ("conteudo", "Matemática"),
            ("conteudo", "Matemática"), ("conteudo", "Física"),
            # fallback da questão 4 para a matéria secundária, depois da passada principal
            ("conteudo", "Física"),
            # subconteúdo agrupado pelo conteúdo pai: triângulo (1, 5), áreas (3), MHS (2, 4)
            ("subconteudo", "Trigonometria no triângulo"), ("subconteudo", "Trigonometria no triângulo"),
            ("subconteudo", "Áreas de figuras planas"),
            ("subconteudo", "Movimento harmônico simples"), ("subconteudo", "Movimento harmônico simples")])
        self.assertEqual([user for _, user in provider.calls[5:9]],
                         [provider.calls[n][1] for n in (0, 2, 3, 4)])
        self.assertEqual(sorted(finished), [1, 2, 3, 4, 5])
        self.assertEqual(results[1]["conteudo_principal_id"],
                         "matematica.trigonometria-no-triangulo.lei-dos-senos")
        self.assertEqual(results[3]["conteudo_principal_id"],
                         "matematica.areas-de-figuras-planas.areas-de-poligonos")
        self.assertEqual(results[2]["materia"], "Física")
        self.assertEqual(results[4]["materia"], "Física")
        self.assertEqual(results[4]["materia_reroteada_de"], "Matemática")
        self.assertEqual(results[4]["tentativas"], 3)
        self.assertEqual(results[4]["conteudo_principal_id"],
                         "fisica.movimento-harmonico-simples.pendulo-simples")
        self.assertEqual(results[1]["roteamento"], "hierarchical-stage")
        # Mesmo resultado que o roteamento questão a questão, só a ordem das chamadas muda.
        for n, text in texts.items():
            single = service.classify_routed(StageProvider(), {"numero": n, "enunciado": text, "alternativas": {}},
                                             None, service.DEFAULT_TAXONOMY, [])
            self.assertEqual({**results[n], "roteamento": "hierarchical"}, single, n)

    def test_stage_major_routing_isolates_errors_and_marks_declined_refinement(self):
        provider = StageProvider(invalid_subject={"quebrada"}, decline={"senos"})
        items = [{"key": n, "question": {"numero": n, "enunciado": text, "alternativas": {}}}
                 for n, text in ((1, "questão quebrada"), (2, "triângulo senos"))]
        results = service.classify_stage_major(provider, items, service.DEFAULT_TAXONOMY)
        self.assertIsInstance(results[1], service.LLMError)
        self.assertTrue(results[2]["subconteudo_pendente"])
        self.assertEqual(results[2]["conteudo_principal_id"], "matematica.content.trigonometria-no-triangulo")
        with self.assertRaises(InterruptedError):
            service.classify_stage_major(StageProvider(), items, service.DEFAULT_TAXONOMY,
                                         cancel_check=lambda: True)

    def test_classificar_uses_stage_major_order_for_ollama_when_flag_is_on(self):
        from unittest import mock
        provider = StageOllama(invalid_subject={"quebrada"})
        events = []
        with tempfile.TemporaryDirectory() as temp:
            exam = Path(temp) / "questoes.json"
            exam.write_text(json.dumps({"prova": "t", "questoes": [
                {"numero": 1, "enunciado": "triângulo lei dos senos", "alternativas": {}},
                {"numero": 2, "enunciado": "pêndulo", "alternativas": {}},
                {"numero": 3, "enunciado": "triângulo área", "alternativas": {}},
                {"numero": 4, "enunciado": "questão quebrada", "alternativas": {}}]}), encoding="utf-8")
            with mock.patch.object(service, "STAGE_ORDER", True), \
                    mock.patch.object(service, "CLASSIFICATION_ROUTING", "hierarchical"):
                data = service.classificar(exam, provider, service.DEFAULT_TAXONOMY, workers=1,
                                           use_cache=False, use_rag=False, log=lambda *_: None,
                                           progress=events.append)
        self.assertEqual([stage for stage, _ in provider.order()],
                         ["materia"] * 5 + ["conteudo"] * 3 + ["subconteudo"] * 3)
        by_number = {q["numero"]: q for q in data["questoes"]}
        self.assertEqual(by_number[1]["classificacao"]["roteamento"], "hierarchical-stage")
        self.assertEqual(by_number[2]["materia"], "Física")
        self.assertIn("classificacao_erro", by_number[4])
        self.assertEqual(data["classificacao"]["erros"].keys(), {4})
        completed = [event for event in events if "question" in event]
        self.assertEqual(len(completed), 4)
        self.assertEqual(completed[-1]["completed"], 4)

    def test_evaluator_attributes_stage_major_usage_to_each_question(self):
        from unittest import mock
        from studai.tools.evaluate_classifier import evaluate
        provider = StageOllama()
        with tempfile.TemporaryDirectory() as temp:
            dataset = Path(temp) / "eval.jsonl"
            dataset.write_text("\n".join(json.dumps({"id": str(n), "question": text}, ensure_ascii=False)
                                         for n, text in ((1, "triângulo senos"), (2, "pêndulo"),
                                                         (3, "triângulo área"))), encoding="utf-8")
            with mock.patch.object(service, "STAGE_ORDER", True), \
                    mock.patch.object(service, "CLASSIFICATION_ROUTING", "hierarchical"), \
                    mock.patch("builtins.print"):
                result = evaluate(dataset, provider, output=Path(temp) / "out.json", source="t",
                                  version="t", allow_unlabeled=True)
        self.assertEqual([stage for stage, _ in provider.order()],
                         ["materia"] * 3 + ["conteudo"] * 3 + ["subconteudo"] * 3)
        self.assertEqual([item["pred_subject"] for item in result["items"]], ["Matemática", "Física", "Matemática"])
        self.assertTrue(all(item["usage"]["calls"] == 3 for item in result["items"]))
        self.assertEqual(result["stages"]["materia"]["calls"], 3)
        self.assertEqual(result["usage"]["prompt_tokens"], 90)

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
        self.users = []

    @property
    def label(self) -> str:
        return "scripted"

    def complete(self, system, user, images=None):
        self.systems.append(system)
        self.users.append(user)
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


class StageProvider(BatchProvider):
    """Single-question answers by keyword; "oscila" needs the secondary-subject fallback."""
    SUBJECT = {**BatchProvider.SUBJECT,
               "oscila": ("Física", "fisica.content.movimento-harmonico-simples",
                          "fisica.movimento-harmonico-simples.pendulo-simples")}

    def __init__(self, invalid_subject=(), decline=()):
        super().__init__()
        self.invalid_subject = set(invalid_subject)
        self.decline = set(decline)

    def _label(self, text):
        if any(key in text for key in self.invalid_subject):
            return ("Astrologia", "", "")
        return super()._label(text)

    def _answer(self, system, text):
        if "identifica a matéria" in system and "oscila" in text:
            return {"materia": "Matemática", "materias_secundarias": ["Física"], "confianca": 0.6}
        if "classifica o conteúdo de questões de Matemática" in system and "oscila" in text:
            return {"conteudo_principal_id": "REVISAR", "conteudos_relacionados_ids": [], "confianca": 0.2}
        if "refina" in system and any(key in text for key in self.decline):
            return {"subconteudo_id": "REVISAR", "confianca": 0.1}
        subject, content, leaf = self._label(text)
        if "identifica a matéria" in system:
            return {"materia": subject, "materias_secundarias": [], "confianca": 0.9}
        return super()._answer(system, text)

    def order(self):
        import re
        stages = []
        for system, _ in self.calls:
            if "identifica a matéria" in system:
                stages.append(("materia", None))
            elif match := re.search(r"conteúdo de questões de (.+?) do vestibular", system):
                stages.append(("conteudo", match.group(1)))
            else:
                path = re.search(r"classificada no conteúdo: (.+)", system).group(1)
                stages.append(("subconteudo", path.split(" → ")[-1]))
        return stages


class StageOllama(StageProvider, service.OllamaProvider):
    def complete_json(self, system, user, schema, images=None):
        history = getattr(self._thread_state, "usage_history", None)
        if history is None:
            history = self._thread_state.usage_history = []
        history.append({"prompt_tokens": 10, "stage": service.REQUEST_STAGE.get()})
        return self.complete(system, user, images)


if __name__ == "__main__":
    unittest.main()
