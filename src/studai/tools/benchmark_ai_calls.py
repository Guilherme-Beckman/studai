"""Conta chamadas de IA (LLM e embeddings) em cenários de reprocessamento, sem rede.

Usa um provedor Ollama simulado e embeddings simulados: mede quantas chamadas cada fluxo faria,
não a qualidade nem o tempo de um modelo real. Tudo roda num diretório temporário
(``STUDAI_CACHE_DIR`` apontado para ele), então o cache real não é lido nem alterado.

    PYTHONPATH=src python -m studai.tools.benchmark_ai_calls
    PYTHONPATH=src python -m studai.tools.benchmark_ai_calls --exam simulados/processados/.../questoes.json
    python benchmark.py ai-calls --json saida.json

O script só usa funções públicas que já existiam antes do cache de IA, para que a mesma
medição rode também numa cópia antiga de ``src/`` (comparação antes/depois).
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

SUBJECT_TEXTS = {
    "Matemática": "Calcule a probabilidade de sortear um triângulo e a função associada ao prisma {i}.",
    "Física": "Um carro com velocidade constante tem energia cinética e força resultante no trecho {i}.",
    "Química": "A reação libera um mol de gás; calcule o ph da solução e o ácido formado no frasco {i}.",
    "Biologia": "A mitocondria e o hormônio da espécie mostram mutação genética na população {i}.",
    "História": "O regime imperial do século XIX e a revolução que o encerrou no episódio {i}.",
    "Geografia": "O bioma e o clima do território influenciam a ferrovia da região {i}.",
}


def synthetic_exam(path: Path, questions: int) -> Path:
    subjects = list(SUBJECT_TEXTS)
    data = {"prova": "benchmark-sintetico", "questoes": [
        {"numero": n, "enunciado": SUBJECT_TEXTS[subjects[(n - 1) % len(subjects)]].format(i=n),
         "alternativas": {letter: {"texto": f"opção {letter}{n}"} for letter in "ABCDE"},
         "revisar": [], "imagens_completas": []}
        for n in range(1, questions + 1)]}
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def _reviewed_examples(path: Path, count: int) -> Path:
    from studai.features.classification.taxonomy import CONTENT_TAXONOMY_VERSION, DEFAULT_CONTENTS_BY_SUBJECT
    rows = []
    subjects = list(SUBJECT_TEXTS)
    for index in range(count):
        subject = subjects[index % len(subjects)]
        content = DEFAULT_CONTENTS_BY_SUBJECT[subject][0]
        rows.append({"id": f"exemplo-{index}", "year": 2020, "exam": "acervo-exemplos",
                     "question": SUBJECT_TEXTS[subject].format(i=f"exemplo {index}"),
                     "subject": subject, "topic": content["name"], "content_id": content["id"],
                     "taxonomy_version": CONTENT_TAXONOMY_VERSION, "reviewed": True})
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> dict:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--exam", type=Path, help="questoes.json já extraído (padrão: prova sintética)")
    parser.add_argument("--questions", type=int, default=30, help="tamanho da prova sintética")
    parser.add_argument("--examples", type=int, default=12, help="exemplos revisados no índice RAG")
    parser.add_argument("--json", type=Path, help="grava o resultado em JSON")
    args = parser.parse_args(argv)

    workdir = Path(tempfile.mkdtemp(prefix="studai-ai-calls-"))
    previous_env = os.environ.get("STUDAI_CACHE_DIR")
    os.environ["STUDAI_CACHE_DIR"] = str(workdir / "cache")
    try:
        return _run(args, workdir)
    finally:
        if previous_env is None:
            os.environ.pop("STUDAI_CACHE_DIR", None)
        else:
            os.environ["STUDAI_CACHE_DIR"] = previous_env
        shutil.rmtree(workdir, ignore_errors=True)


def _run(args, workdir: Path) -> dict:
    from studai.features.answer_keys.service import associate_answer_key
    from studai.features.classification import service as classification
    from studai.features.classification.cache import ClassificationCache
    from studai.features.classification.rag import index_examples
    from studai.features.diagnostics import service as diagnostics
    from studai.integrations.llm import REQUEST_STAGE, DemoProvider, LLMError, MockProvider, OllamaProvider

    counts: collections.Counter = collections.Counter()
    heuristic = MockProvider(model="")
    demo = DemoProvider(model="")

    class FakeOllama(OllamaProvider):
        """Responde cada etapa pelo schema pedido, sem rede."""

        def complete_json(self, system, user, schema, images=None):
            stage = REQUEST_STAGE.get() or "sem_etapa"
            counts[f"llm.{stage}"] += 1
            props = (schema or {}).get("properties", {})
            if "subconteudo_id" in props:
                return json.dumps({"subconteudo_id": "MANTER", "confianca": 0.9})
            if "conteudo_principal_id" in props and "materia" not in props:
                ids = [value for value in props["conteudo_principal_id"]["enum"] if value != "REVISAR"]
                return json.dumps({"conteudo_principal_id": ids[0] if ids else "REVISAR",
                                   "conteudos_relacionados_ids": [], "confianca": 0.9})
            guess = json.loads(heuristic.complete(system, user))
            return json.dumps({"materia": guess["materia"], "materias_secundarias": [], "confianca": 0.9})

        def complete(self, system, user, images=None):
            if "interpreta resultados de um diagnóstico" in system:
                counts["llm.diagnostico_analise"] += 1
                return demo.complete(system, user)
            if "associa arquivos de provas e gabaritos" in system:
                counts["llm.gabarito_associacao"] += 1
                payload = json.loads(user[user.index('{"prova"'):])
                exam_id = payload["prova"]["identificador"]
                match = next((item["arquivo"] for item in payload["gabaritos"]
                              if any(year in item["identificador"] for year in exam_id.split("-") if year.isdigit())),
                             None)
                return json.dumps({"arquivo": match, "confianca": 0.95})
            return self.complete_json(system, user, None, images)

    def fake_embed(texts, **_):
        counts["embedding.request"] += 1
        counts["embedding.texts"] += len(texts)
        return [[float(len(text) % 7), 1.0, float(sum(map(ord, text)) % 11)] for text in texts]

    exam = workdir / "prova" / "questoes.json"
    exam.parent.mkdir(parents=True)
    if args.exam:
        shutil.copyfile(args.exam, exam)
    else:
        synthetic_exam(exam, args.questions)
    total = len(json.loads(exam.read_text(encoding="utf-8"))["questoes"])
    examples = _reviewed_examples(workdir / "reviewed.jsonl", args.examples)
    cache_path = workdir / "cache" / "classification-cache.sqlite3"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    provider = FakeOllama(model="fake-qwen", base_url="http://ollama.invalid")
    scenarios = []

    def measure(name: str, description: str, action) -> None:
        counts.clear()
        started = time.perf_counter()
        extra = action() or {}
        row = {"scenario": name, "description": description, "seconds": round(time.perf_counter() - started, 3),
               "llm_calls": sum(v for k, v in counts.items() if k.startswith("llm.")),
               "embedding_requests": counts["embedding.request"], "embedding_texts": counts["embedding.texts"],
               "by_stage": {k: v for k, v in sorted(counts.items()) if k.startswith("llm.")}, **extra}
        scenarios.append(row)

    def reindex(count: int) -> None:
        _reviewed_examples(examples, count)
        cache = ClassificationCache(cache_path)
        try:
            index_examples(examples, cache, base_url="http://ollama.invalid", model="embed-bench")
        finally:
            cache.close()

    def classify() -> dict:
        result = classification.classificar(exam, provider, classification.DEFAULT_TAXONOMY, workers=4,
                                            log=lambda *_: None, embedding_model="embed-bench",
                                            retry_delays=(0,), sleep=lambda _s: None)
        info = result.get("processamento_cache", {})
        return {"questions": total, "cache_hits": info.get("questoes_reutilizadas", 0),
                "errors": len(result.get("classificacao", {}).get("erros", {})),
                "rag": result.get("classificacao", {}).get("rag")}

    with patch("studai.features.classification.rag.ollama_embed", side_effect=fake_embed), \
         patch.object(classification, "DEFAULT_CLASSIFICATION_CACHE", cache_path), \
         patch.object(classification, "DEFAULT_EXAMPLES", examples), \
         patch.object(classification, "DEFAULT_QUESTIONS_DIR", workdir / "sem-acervo"), \
         patch.object(classification, "_CACHE_WARMED", True):
        measure("indice_rag", f"Indexa {args.examples} exemplos revisados", lambda: reindex(args.examples))
        measure("classificacao_inicial", "1ª classificação da prova com RAG", classify)
        measure("reprocessar_igual", "Mesma prova, mesma configuração", classify)
        measure("indice_rag_alterado", "Um exemplo revisado a mais (nova revisão)",
                lambda: reindex(args.examples + 1))
        measure("reprocessar_apos_revisao", "Mesma prova depois da revisão: RAG muda, cache da classificação não vale",
                classify)
        # Falha transitória na 1ª chamada de cada questão: a rodada de reprocessamento repete tudo.
        reindex(args.examples + 2)
        original = FakeOllama.complete_json
        failed: set[int] = set()

        def flaky(self, system, user, schema, images=None):
            stage = REQUEST_STAGE.get()
            key = hash(user.split("EXEMPLOS DE REFERÊNCIA")[0])
            if stage == "materia" and key not in failed:
                failed.add(key)
                counts[f"llm.{stage}"] += 1
                raise LLMError("HTTP 503: simulado", status_code=503)
            return original(self, system, user, schema, images)
        with patch.object(FakeOllama, "complete_json", flaky):
            measure("reprocessar_com_falha", "Classificação em que cada questão falha uma vez e volta na rodada 2",
                    classify)

    exam_pdf = Path("fuvest-2024-primeira-fase-V1.pdf")
    keys = [Path("gabarito-fuvest-2024-primeira-fase-v1-oficial.pdf"), Path("gabarito-fuvest-2023.pdf")]
    measure("gabarito_lote_2x", "Associação ambígua de gabarito em duas execuções do lote",
            lambda: [associate_answer_key(exam_pdf, keys, provider) for _ in range(2)] and None)
    metrics = {"subjects": {"Matemática": {"attempts": 3, "correct": 1, "accuracy": 1 / 3,
               "error_reasons": {"unknown_content": 1, "calc_error": 1, "misread": 0, "blank": 0},
               "status": "assessed"}}}
    profile = {"nome": "Curso", "pesos": {"Matemática": 1}}
    measure("analise_diagnostica_2x", "Concluir a mesma tentativa duas vezes",
            lambda: [diagnostics.analyze(provider, metrics, profile) for _ in range(2)] and None)

    try:
        from studai.integrations import ai_cache
        cache_mode = "ai_cache"
        del ai_cache
    except ImportError:
        cache_mode = "sem ai_cache"
    report = {"questions": total, "examples": args.examples, "exam": "custom" if args.exam else "synthetic",
              "code": cache_mode, "scenarios": scenarios}
    print(f"Prova: {total} questões ({report['exam']}); código: {cache_mode}")
    print(f"{'cenário':28} {'LLM':>5} {'emb req':>8} {'emb txt':>8} {'cache':>6} {'erros':>6}")
    for row in scenarios:
        print(f"{row['scenario']:28} {row['llm_calls']:>5} {row['embedding_requests']:>8} "
              f"{row['embedding_texts']:>8} {row.get('cache_hits', '-')!s:>6} {row.get('errors', '-')!s:>6}")
    if args.json:
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    main(sys.argv[1:])
