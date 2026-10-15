import copy
import json
import tempfile
import unittest
from collections import defaultdict
from pathlib import Path

from studai.features.classification.taxonomy import (
    CONTENT_INDEX, CONTENT_TAXONOMY_VERSION, DEFAULT_CONTENTS_BY_SUBJECT, DEFAULT_CONTENT_TAXONOMY,
    STAGE_ONE_OPTIONS, aggregate_content_ids, canonical_content_id, content_path, is_stage_one_option,
    load_content_taxonomy, public_content_taxonomy, refinement_options, stage_one_id,
    validate_content_taxonomy,
)
from studai.integrations.llm import Provider
from studai.features.classification import service


class ContentTaxonomyTests(unittest.TestCase):
    def test_tree_has_thirteen_subjects_unique_nodes_sources_and_all_legacy_labels(self):
        self.assertEqual(len(DEFAULT_CONTENT_TAXONOMY["subjects"]), 13)
        self.assertEqual(len(CONTENT_INDEX), len(set(CONTENT_INDEX)))
        # Nomes legados continuam resolvendo, como nome ou alias, mesmo após renomeações.
        for subject, topics in service.LEGACY_TOPIC_TAXONOMY.items():
            for topic in topics:
                self.assertIsNotNone(canonical_content_id(topic, subject=subject, allow_refinable=True),
                                     f"{subject}: {topic}")
        for node in CONTENT_INDEX.values():
            self.assertTrue(node["description"].strip())
            self.assertTrue(node["sources"], node["id"])

    def test_double_arc_is_a_leaf_under_trigonometric_identities_with_derived_source(self):
        content_id = "matematica.content.arco-duplo"
        self.assertEqual(content_path(content_id),
                         "Matemática → Trigonometria → Identidades e equações trigonométricas → Arco duplo")
        node = CONTENT_INDEX[content_id]
        self.assertTrue(node["editorial_detail"])
        self.assertIn("fórmula do ângulo duplo", [alias.casefold() for alias in node["aliases"]])
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
        leaves = [node for node in CONTENT_INDEX.values()
                  if node["is_leaf"] and not node.get("deprecated")]
        self.assertGreater(len(leaves), 1400)
        for node in leaves:
            self.assertEqual(node.get("level"), "subcontent", node["id"])
            self.assertEqual(len(node["path"]), 4, node["id"])
            if not node.get("deprecated"):
                self.assertEqual(stage_one_id(node["id"]), node["path_ids"][2])
        self.assertTrue(all(node["refinable"] for node in STAGE_ONE_OPTIONS))
        self.assertEqual(len({node["subject"] for node in leaves}), 13)
        self.assertEqual(content_path("fisica.ondas-sonoras-e-nivel-de-intensidade.efeito-doppler"),
                         "Física → Oscilações e ondas → Ondas sonoras e nível de intensidade → Efeito Doppler")
        self.assertEqual(content_path("historia.revolucao-francesa.era-napoleonica"),
                         "História → Europa moderna, revoluções e capitalismo (séculos XV–XIX) → Revolução Francesa → Era napoleônica")

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


def _raw_nodes():
    """Yield (node, ancestors, depth) for every node of the shipped tree, subjects at depth 1."""
    def walk(nodes, ancestors):
        for node in nodes:
            yield node, ancestors, len(ancestors) + 1
            yield from walk(node.get("children") or [], (*ancestors, node))
    yield from walk(public_content_taxonomy()["subjects"], ())


def _subjects():
    return public_content_taxonomy()["subjects"]


class ContentTaxonomyStructureTests(unittest.TestCase):
    """Structural guards for the shipped tree; no editorial counts are hardcoded."""

    LEVEL_BY_DEPTH = {1: ("subject", None), 2: ("area", None), 3: ("content",), 4: ("subcontent",)}

    def test_version_matches_fuvest_edition_pattern(self):
        version = DEFAULT_CONTENT_TAXONOMY.get("version")
        self.assertIsInstance(version, str)
        self.assertRegex(version, r"^fuvest-2027\.v[1-9]\d*$")
        self.assertEqual(CONTENT_TAXONOMY_VERSION, version)

    def test_all_ids_globally_unique_including_subjects(self):
        ids = [node.get("id") for node, _, _ in _raw_nodes()]
        duplicates = sorted({identity for identity in ids if ids.count(identity) > 1})
        self.assertEqual(duplicates, [])
        self.assertTrue(all(isinstance(identity, str) and identity for identity in ids))
        subject_ids = {subject["id"] for subject in _subjects()}
        self.assertEqual(set(CONTENT_INDEX), set(ids) - subject_ids)

    def test_every_id_is_prefixed_by_its_subject_id(self):
        for node, ancestors, _ in _raw_nodes():
            if not ancestors:
                self.assertRegex(node["id"], r"^[a-z0-9-]+$", node["id"])
                continue
            self.assertTrue(node["id"].startswith(ancestors[0]["id"] + "."), node["id"])
            self.assertEqual(CONTENT_INDEX[node["id"]]["subject_id"], ancestors[0]["id"])
            self.assertEqual(CONTENT_INDEX[node["id"]]["subject"], ancestors[0]["name"])

    def test_path_ids_and_path_match_ancestry(self):
        for node, ancestors, _ in _raw_nodes():
            if not ancestors:
                continue
            indexed = CONTENT_INDEX[node["id"]]
            chain = (*ancestors, node)
            self.assertEqual(indexed["path_ids"], [part["id"] for part in chain], node["id"])
            self.assertEqual(indexed["path"], [part["name"] for part in chain], node["id"])
            self.assertEqual(indexed["subject_id"], indexed["path_ids"][0])
            self.assertEqual(indexed["is_leaf"], not node.get("children"), node["id"])
            self.assertEqual(content_path(node["id"]), " → ".join(indexed["path"]))
            for parent_id in indexed["path_ids"][1:-1]:
                self.assertIn(node["id"], [child["id"] for child in
                                           _descendants(CONTENT_INDEX[parent_id])])

    def test_every_node_has_name_description_and_complete_sources(self):
        for node, _, _ in _raw_nodes():
            identity = node["id"]
            self.assertIsInstance(node.get("name"), str, identity)
            self.assertTrue(node["name"].strip(), identity)
            self.assertEqual(node["name"], node["name"].strip(), identity)
            self.assertIsInstance(node.get("description"), str, identity)
            self.assertTrue(node["description"].strip(), identity)
            self.assertIsInstance(node.get("sources"), list, identity)
            self.assertTrue(node["sources"], identity)
            for source in node["sources"]:
                self.assertIsInstance(source, dict, identity)
                self.assertTrue(str(source.get("title") or "").strip(), f"{identity}: fonte sem title")
                self.assertTrue(str(source.get("url") or source.get("file") or "").strip(),
                                f"{identity}: fonte sem url/file")
            aliases = node.get("aliases", [])
            self.assertIsInstance(aliases, list, identity)
            self.assertTrue(all(isinstance(alias, str) and alias.strip() for alias in aliases), identity)

    def test_level_discipline_subject_area_content_subcontent(self):
        for node, _, depth in _raw_nodes():
            identity = node["id"]
            self.assertIn(depth, self.LEVEL_BY_DEPTH, f"{identity}: profundidade {depth}")
            self.assertIn(node.get("level"), self.LEVEL_BY_DEPTH[depth], identity)
            children = node.get("children") or []
            if depth <= 2:
                self.assertTrue(children, f"{identity}: matéria/área sem filhos")
            if depth == 3:
                # A content either is a plain leaf or only holds childless subcontents.
                self.assertTrue(all(child.get("level") == "subcontent" and not child.get("children")
                                    for child in children), identity)
                self.assertEqual(CONTENT_INDEX[identity]["refinable"], bool(children), identity)
            if depth == 4:
                self.assertEqual(children, [], identity)
                self.assertIs(node.get("editorial_detail"), True, identity)
            elif "editorial_detail" in node:
                self.fail(f"{identity}: editorial_detail fora de subconteúdo")

    def test_no_siblings_share_a_casefolded_name(self):
        def check(parent_id, children):
            names = defaultdict(list)
            for child in children:
                names[child["name"].strip().casefold()].append(child["id"])
            clashes = {name: ids for name, ids in names.items() if len(ids) > 1}
            self.assertEqual(clashes, {}, parent_id)
        check("<raiz>", _subjects())
        for node, _, _ in _raw_nodes():
            check(node["id"], node.get("children") or [])

    def test_aliases_do_not_collide_within_subject_and_selectable_level(self):
        """Names/aliases must resolve to one node per subject at each selectable level.

        Levels: stage-one options (what the 1st step and ``canonical_content_id`` with
        ``allow_refinable`` pick) and leaves (what ``canonical_content_id`` resolves).
        """
        groups = {"etapa1": is_stage_one_option, "folha": lambda node: node["is_leaf"]}
        violations = []
        for label, selectable in groups.items():
            terms = defaultdict(set)
            for node in CONTENT_INDEX.values():
                if not selectable(node):
                    continue
                for term in {node["name"], *node.get("aliases", [])}:
                    terms[(node["subject_id"], term.strip().casefold())].add(node["id"])
            violations += [f"{label} {subject}: {term!r} -> {sorted(ids)}"
                           for (subject, term), ids in sorted(terms.items()) if len(ids) > 1]
        self.assertEqual(violations, [], "\n" + "\n".join(violations))

    def test_stage_one_and_refinement_options_are_consistent(self):
        option_ids = [node["id"] for node in STAGE_ONE_OPTIONS]
        self.assertEqual(len(option_ids), len(set(option_ids)))
        self.assertTrue(option_ids)
        for node in STAGE_ONE_OPTIONS:
            self.assertNotEqual(node.get("level"), "subcontent", node["id"])
            self.assertEqual(stage_one_id(node["id"]), node["id"])
            options = refinement_options(node["id"])
            self.assertEqual(bool(options), node["refinable"], node["id"])
            self.assertEqual([child["id"] for child in options],
                             [child["id"] for child in node.get("children") or []
                              if not child.get("deprecated")])
            for child in options:
                self.assertEqual(child["level"], "subcontent")
                self.assertTrue(child["is_leaf"])
                self.assertEqual(child["path_ids"][:-1], node["path_ids"], child["id"])
                self.assertEqual(stage_one_id(child["id"]), node["id"])
            self.assertEqual(canonical_content_id(node["id"], allow_refinable=True), node["id"])
        for identity, node in CONTENT_INDEX.items():
            if node.get("deprecated"):
                # IDs obsoletos continuam válidos e resolvem para o substituto.
                self.assertNotIn(identity, option_ids)
                self.assertEqual(stage_one_id(identity), stage_one_id(node["replaced_by"]), identity)
                continue
            if node["is_leaf"]:
                self.assertIn(stage_one_id(identity), option_ids, identity)
                self.assertEqual(canonical_content_id(identity), identity)
                self.assertEqual(refinement_options(identity), [])
            elif not node["refinable"]:
                self.assertIsNone(stage_one_id(identity), identity)
                self.assertEqual(refinement_options(identity), [])
            self.assertEqual(is_stage_one_option(node), identity in option_ids, identity)
        for subject in _subjects():
            self.assertIsNone(stage_one_id(subject["id"]))
            self.assertTrue(any(node["subject_id"] == subject["id"] for node in STAGE_ONE_OPTIONS),
                            subject["id"])
        self.assertIsNone(stage_one_id("inexistente.id"))
        self.assertEqual(refinement_options("inexistente.id"), [])

    def test_deprecated_nodes_point_to_live_replacement_in_same_subject(self):
        for node, ancestors, _ in _raw_nodes():
            if "deprecated" not in node and "replaced_by" not in node:
                continue
            identity = node["id"]
            self.assertIs(node.get("deprecated"), True, f"{identity}: replaced_by sem deprecated")
            target = node.get("replaced_by")
            self.assertIsInstance(target, str, f"{identity}: deprecated sem replaced_by")
            self.assertNotEqual(target, identity)
            self.assertIn(target, CONTENT_INDEX, f"{identity}: replaced_by inexistente {target!r}")
            replacement = CONTENT_INDEX[target]
            self.assertFalse(replacement.get("deprecated"), f"{identity}: substituto {target} obsoleto")
            subject_id = ancestors[0]["id"] if ancestors else identity
            self.assertEqual(replacement["subject_id"], subject_id, f"{identity}: substituto de outra matéria")


def _descendants(node):
    for child in node.get("children") or []:
        yield child
        yield from _descendants(child)


def _sample():
    source = [{"title": "Programa", "url": "https://example.test/programa.pdf"}]
    return {"version": "fuvest-2027.v1", "subjects": [
        {"id": "s", "name": "S", "description": "matéria", "sources": source, "children": [
            {"id": "s.area.a", "name": "A", "description": "área", "sources": source, "children": [
                {"id": "s.content.c", "name": "C", "description": "conteúdo", "level": "content",
                 "sources": source, "children": [
                     {"id": "s.c.x", "name": "X", "description": "sub", "level": "subcontent",
                      "editorial_detail": True, "sources": source, "children": []}]},
                {"id": "s.content.d", "name": "D", "description": "folha", "level": "content",
                 "sources": source, "children": []}]}]}]}


class ContentTaxonomyValidatorTests(unittest.TestCase):
    def assertRejected(self, payload, pattern):
        with self.assertRaisesRegex(ValueError, pattern):
            validate_content_taxonomy(payload)

    def test_valid_sample_builds_index_with_paths_and_refinable_flags(self):
        index = validate_content_taxonomy(_sample())
        self.assertEqual(set(index), {"s.area.a", "s.content.c", "s.c.x", "s.content.d"})
        self.assertEqual(index["s.c.x"]["path_ids"], ["s", "s.area.a", "s.content.c", "s.c.x"])
        self.assertEqual(index["s.c.x"]["path"], ["S", "A", "C", "X"])
        self.assertTrue(index["s.content.c"]["refinable"])
        self.assertFalse(index["s.content.d"]["refinable"])
        self.assertTrue(index["s.content.d"]["is_leaf"])

    def test_rejects_missing_or_empty_subjects(self):
        self.assertRejected({}, "lista não vazia")
        self.assertRejected({"subjects": []}, "lista não vazia")
        self.assertRejected({"subjects": "s"}, "lista não vazia")

    def test_rejects_subject_without_id_or_name(self):
        for key in ("id", "name"):
            payload = _sample()
            del payload["subjects"][0][key]
            self.assertRejected(payload, "id e name")
        self.assertRejected({"subjects": ["s"]}, "id e name")

    def test_rejects_duplicate_subject_id(self):
        payload = _sample()
        twin = copy.deepcopy(payload["subjects"][0])
        twin["children"] = [{**twin["children"][0], "id": "t.area"}]
        twin["children"][0]["children"] = [{**twin["children"][0]["children"][1], "id": "t.leaf"}]
        payload["subjects"].append(twin)
        self.assertRejected(payload, "duplicado")

    def test_rejects_node_id_colliding_with_subject_id(self):
        payload = _sample()
        payload["subjects"][0]["children"][0]["children"][1]["id"] = "s"
        self.assertRejected(payload, "duplicado")

    def test_rejects_subject_without_children_description_or_sources(self):
        for key, value, pattern in (("children", [], "sem áreas"), ("description", "", "descrição ou fonte"),
                                    ("sources", [], "descrição ou fonte")):
            payload = _sample()
            payload["subjects"][0][key] = value
            self.assertRejected(payload, pattern)

    def test_rejects_node_with_missing_or_empty_id(self):
        for value in (None, "", 7):
            payload = _sample()
            payload["subjects"][0]["children"][0]["children"][1]["id"] = value
            self.assertRejected(payload, "id ausente")

    def test_rejects_node_without_name_or_description(self):
        for key, pattern in (("name", "sem name"), ("description", "sem description")):
            for value in (None, "   "):
                payload = _sample()
                payload["subjects"][0]["children"][0]["children"][1][key] = value
                self.assertRejected(payload, pattern)

    def test_rejects_non_list_children(self):
        payload = _sample()
        payload["subjects"][0]["children"][0]["children"][1]["children"] = {"id": "x"}
        self.assertRejected(payload, "children deve ser lista")

    def test_rejects_area_without_children(self):
        payload = _sample()
        payload["subjects"][0]["children"][0]["children"] = []
        self.assertRejected(payload, "área sem conteúdo")

    def test_rejects_invalid_aliases(self):
        for aliases in ("texto", [1], [None]):
            payload = _sample()
            payload["subjects"][0]["children"][0]["children"][1]["aliases"] = aliases
            self.assertRejected(payload, "aliases inválidos")

    def test_rejects_invalid_or_missing_node_sources(self):
        for sources, pattern in (("url", "sources inválidos"), (["url"], "sources inválidos"),
                                 ([], "sem fonte"), (None, "sem fonte")):
            payload = _sample()
            node = payload["subjects"][0]["children"][0]["children"][1]
            if sources is None:
                del node["sources"]
            else:
                node["sources"] = sources
            self.assertRejected(payload, pattern)

    def test_loader_requires_version(self):
        payload = _sample()
        del payload["version"]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "taxonomy.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "version"):
                load_content_taxonomy(path)
            path.write_text(json.dumps(_sample()), encoding="utf-8")
            loaded = load_content_taxonomy(path)
        self.assertIn("s.c.x", loaded["index"])


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
