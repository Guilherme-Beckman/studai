"""Regras de matéria de Linguagens e classificador leve de matéria (provedores e embeddings simulados)."""
import json
import tempfile
import unittest
from pathlib import Path

from studai.features.classification import service
from studai.features.classification.subject_model import (
    SubjectClassifier, SubjectModel, cross_validate, question_text, train_subject_model,
)
from studai.integrations.llm import Provider
from studai.tools import evaluate_classifier, train_subject_classifier


class RecordingProvider(Provider):
    def __init__(self, answers):
        self.answers = list(answers)
        self.systems = []
        self.users = []

    @property
    def label(self) -> str:
        return "recording"

    def complete(self, system, user, images=None):
        self.systems.append(system)
        self.users.append(user)
        return json.dumps(self.answers.pop(0), ensure_ascii=False)


SUBJECTS = ["Inglês", "Literatura", "Matemática"]


def fake_vector(subject: str, noise: float = 0.0) -> list[float]:
    base = {"Inglês": [1.0, 0.0, 0.0, noise], "Literatura": [0.0, 1.0, 0.0, noise],
            "Matemática": [0.0, 0.0, 1.0, noise]}
    return base[subject]


def fake_embed(texts):
    vectors = []
    for text in texts:
        if "the" in text.split():
            vectors.append(fake_vector("Inglês"))
        elif "poema" in text:
            vectors.append(fake_vector("Literatura"))
        elif "equação" in text:
            vectors.append(fake_vector("Matemática"))
        else:
            vectors.append([0.5, 0.5, 0.5, 0.0])
    return vectors


def toy_model() -> SubjectModel:
    vectors, labels = [], []
    for subject in SUBJECTS:
        for noise in (0.0, 0.05, 0.1, 0.15):
            vectors.append(fake_vector(subject, noise))
            labels.append(subject)
    return train_subject_model(vectors, labels, epochs=300, learning_rate=5.0, embedding_model="fake")


class LanguageSubjectRulesTests(unittest.TestCase):
    def test_subject_prompt_has_language_tiebreak_rules_and_richer_descriptions(self):
        system = service.build_subject_system(service.DEFAULT_TAXONOMY)
        self.assertIn("poema ou letra de canção", system)
        self.assertIn("eu lírico", system)
        self.assertIn("eu lírico", system)
        self.assertIn("figura de linguagem", system)
        # Descrições das matérias também guiam o desempate.
        self.assertIn("lista de leitura obrigatória", system)
        self.assertIn("lista de leitura obrigatória", system)
        self.assertNotIn("{regras_linguagens}", system)

    def test_flat_prompt_has_the_same_rules(self):
        system = service.build_system(service.DEFAULT_TAXONOMY)
        self.assertIn(service.LANGUAGE_SUBJECT_RULES, system)
        self.assertNotIn("{regras_linguagens}", system)

    def test_routed_classifier_sends_rules_in_the_subject_call_only(self):
        provider = RecordingProvider([
            {"materia": "Inglês", "materias_secundarias": [], "confianca": 0.9},
            {"conteudo_principal_id": "ingles.content.interpretacao-de-texto-em-ingles",
             "conteudos_relacionados_ids": [], "confianca": 0.8},
            {"subconteudo_id": "MANTER", "confianca": 0.8}])
        question = {"numero": 41, "enunciado": "Na letra da música, o verso “Like you are a poet”",
                    "alternativas": {}}
        result = service.classify_routed(provider, question, None, service.DEFAULT_TAXONOMY, [])
        self.assertEqual(result["materia"], "Inglês")
        self.assertNotIn("materia_origem", result)
        self.assertIn(service.LANGUAGE_SUBJECT_RULES, provider.systems[0])
        self.assertNotIn(service.LANGUAGE_SUBJECT_RULES, provider.systems[1])

    def test_prompt_version_changed_with_the_rules(self):
        self.assertIn("language-rules", service.PROMPT_VERSION)


class SubjectModelTests(unittest.TestCase):
    def test_training_separates_subjects_and_round_trips_through_json(self):
        model = toy_model()
        probabilities = model.predict_proba(fake_vector("Literatura", 0.02))
        self.assertEqual(max(probabilities, key=probabilities.get), "Literatura")
        self.assertAlmostEqual(sum(probabilities.values()), 1.0, places=6)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "model.json"
            model.save(path)
            loaded = SubjectModel.load(path)
        self.assertEqual(loaded.labels, model.labels)
        self.assertEqual(loaded.embedding_model, "fake")
        again = loaded.predict_proba(fake_vector("Literatura", 0.02))
        self.assertAlmostEqual(again["Literatura"], probabilities["Literatura"], places=4)

    def test_dimension_mismatch_is_an_error(self):
        with self.assertRaises(ValueError):
            toy_model().predict_proba([1.0, 0.0])

    def test_cross_validation_reports_accuracy_and_coverage_by_threshold(self):
        vectors, labels, groups = [], [], []
        for exam in ("p1", "p2", "p3"):
            for subject in SUBJECTS:
                for noise in (0.0, 0.1):
                    vectors.append(fake_vector(subject, noise))
                    labels.append(subject)
                    groups.append(exam)
        report = cross_validate(vectors, labels, folds=3, groups=groups, epochs=200, learning_rate=5.0)
        self.assertEqual(report["count"], 18)
        self.assertTrue(report["grouped"])
        self.assertEqual(report["accuracy"], 1.0)
        self.assertIn("0.90", report["by_threshold"])
        self.assertEqual(report["per_subject"]["Inglês"], {"count": 6, "correct": 6})

    def test_classifier_answers_only_above_threshold(self):
        classifier = SubjectClassifier(toy_model(), fake_embed, min_confidence=0.6)
        subject, secondary, conf = classifier({"enunciado": "Read the text", "alternativas": {}})
        self.assertEqual(subject, "Inglês")
        self.assertGreaterEqual(conf, 0.6)
        self.assertNotIn("Inglês", secondary)
        # Vetor ambíguo: abaixo do limiar, o LLM decide.
        self.assertIsNone(classifier({"enunciado": "texto neutro", "alternativas": {}}))
        # Falha de embedding nunca derruba a classificação.
        broken = SubjectClassifier(toy_model(), lambda texts: 1 / 0, min_confidence=0.1)
        self.assertIsNone(broken({"enunciado": "Read the text", "alternativas": {}}))

    def test_routed_classifier_skips_the_subject_call_when_model_is_confident(self):
        classifier = SubjectClassifier(toy_model(), fake_embed, min_confidence=0.6)
        provider = RecordingProvider([
            {"conteudo_principal_id": "ingles.content.interpretacao-de-texto-em-ingles",
             "conteudos_relacionados_ids": [], "confianca": 0.8},
            {"subconteudo_id": "MANTER", "confianca": 0.8}])
        question = {"numero": 3, "enunciado": "What is the main idea of the text?", "alternativas": {}}
        result = service.classify_routed(provider, question, None, service.DEFAULT_TAXONOMY, [],
                                         subject_classifier=classifier)
        self.assertEqual(result["materia"], "Inglês")
        self.assertEqual(result["materia_origem"], "subject_model")
        self.assertEqual(len(provider.systems), 2)
        self.assertNotIn("Você identifica a matéria", provider.systems[0])

    def test_routed_classifier_falls_back_to_llm_when_model_is_unsure(self):
        classifier = SubjectClassifier(toy_model(), fake_embed, min_confidence=0.99)
        provider = RecordingProvider([
            {"materia": "Literatura", "materias_secundarias": [], "confianca": 0.9},
            {"conteudo_principal_id": "REVISAR", "conteudos_relacionados_ids": [], "confianca": 0.1}])
        question = {"numero": 4, "enunciado": "texto neutro", "alternativas": {}}
        result = service.classify_routed(provider, question, None, service.DEFAULT_TAXONOMY, [],
                                         subject_classifier=classifier)
        self.assertEqual(result["materia"], "Literatura")
        self.assertNotIn("materia_origem", result)
        self.assertIn("Você identifica a matéria", provider.systems[0])

    def test_question_text_matches_training_format(self):
        q = {"enunciado": "Pergunta", "nota": "Nota", "alternativas": {"A": {"texto": "um"}}}
        text = question_text(q, {"texto": "Base"})
        self.assertEqual(text, "Pergunta\nTexto de apoio: Base\nNota\n(A) um")


class TrainingCliTests(unittest.TestCase):
    def test_load_embed_and_train_from_jsonl_with_embedding_cache(self):
        rows = []
        for exam in ("p1", "p2"):
            rows += [{"id": f"{exam}:1", "exam": exam, "question": "Read the text", "subject": "Inglês"},
                     {"id": f"{exam}:2", "exam": exam, "question": "Leia o poema", "subject": "Literatura"},
                     {"id": f"{exam}:3", "exam": exam, "question": "Resolva a equação", "subject": "Matemática"},
                     {"id": f"{exam}:4", "exam": exam, "question": "x", "subject": "Matemática",
                      "review_status": "pending"}]
        calls = []

        def embed(texts):
            calls.append(len(texts))
            return fake_embed(texts)

        with tempfile.TemporaryDirectory() as tmp:
            dataset = Path(tmp) / "reviewed.jsonl"
            dataset.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                               encoding="utf-8")
            loaded = train_subject_classifier.load_training_rows([dataset])
            self.assertEqual(len(loaded), 6)
            cache = Path(tmp) / "emb.json"
            vectors = train_subject_classifier.embed_rows(loaded, embed, cache_path=cache, model="fake")
            train_subject_classifier.embed_rows(loaded, embed, cache_path=cache, model="fake")
            self.assertEqual(sum(calls), 6)  # a 2ª rodada vem toda do cache
            model, report, counts = train_subject_classifier.train(loaded, vectors, folds=2, epochs=200,
                                                                   embedding_model="fake")
        self.assertEqual(counts, {"Inglês": 2, "Literatura": 2, "Matemática": 2})
        self.assertEqual(report["count"], 6)
        self.assertEqual(model.meta["trained_on"], 6)


class EvaluateFiltersTests(unittest.TestCase):
    def test_subject_filter_and_subject_only_mode(self):
        content = "ingles.content.interpretacao-de-texto-em-ingles"
        from studai.features.classification.taxonomy import CONTENT_TAXONOMY_VERSION
        rows = [{"id": "a", "question": "Read the text", "subject": "Inglês", "content_id": content,
                 "taxonomy_version": CONTENT_TAXONOMY_VERSION,
                 "alternatives": {"A": {"texto": "one", "figuras": []}}},
                {"id": "b", "question": "Resolva", "subject": "Matemática",
                 "content_id": "matematica.content.arco-duplo", "taxonomy_version": CONTENT_TAXONOMY_VERSION}]
        provider = RecordingProvider([{"materia": "Inglês", "materias_secundarias": [], "confianca": 0.9}])
        with tempfile.TemporaryDirectory() as tmp:
            dataset = Path(tmp) / "set.jsonl"
            dataset.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                               encoding="utf-8")
            result = evaluate_classifier.evaluate(dataset, provider, output=Path(tmp) / "out.json",
                                                  source="t", version="t", subjects=["ingles"],
                                                  subject_only=True)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["subject"]["accuracy"], 1.0)
        self.assertTrue(result["subject_only"])
        self.assertEqual(len(provider.systems), 1)
        # Alternativas no formato novo ({"texto": ...}) chegam ao prompt como texto.
        self.assertIn("(A) one", provider.users[0])


if __name__ == "__main__":
    unittest.main()
