#!/usr/bin/env python3
"""Worker CLI para diagnóstico inicial FUVEST por curso."""
from __future__ import annotations

import argparse
import json
import random
import sys
import threading
import time
from pathlib import Path

from studai.features.classification import service as clf
from studai.features.answer_keys.service import import_key_file as import_key
from studai.features.diagnostics import service as core
from studai.settings import DEFAULT_COURSE_CONFIG, DEFAULT_DIAGNOSTICS_DB, DEFAULT_QUESTIONS_DIR, SERVICE_ROOT

HERE = SERVICE_ROOT
DEFAULT_CONFIG = DEFAULT_COURSE_CONFIG
DEFAULT_DB = DEFAULT_DIAGNOSTICS_DB
DEFAULT_QUESTIONS = DEFAULT_QUESTIONS_DIR


def build_provider(args):
    provider_name = args.provider
    model = args.model
    if not model:
        if provider_name == "ollama":
            model = "qwen3.5:4b"
        elif provider_name == "gemini":
            model = "gemini-3.8-flash,gemini-3.7-flash,gemini-3.6-flash"
        else:
            model = ""
    clf.load_dotenv(HERE / ".env")
    provider = clf.make_provider(provider_name, model, base_url=args.base_url, key_env=args.api_key_env,
                                 temperature=0, max_tokens=1600, json_mode=True,
                                 num_threads=getattr(args, "ollama_threads", 0))
    return VisualProvider(provider, getattr(args, "workers", 1))


class VisualProvider:
    """Exibe atividade e duração em cada chamada longa da LLM."""
    def __init__(self, provider, workers: int):
        self._provider = provider
        self.workers = workers

    def __getattr__(self, name):
        return getattr(self._provider, name)

    def complete(self, system, user, images=None):
        stop = threading.Event()
        started = time.monotonic()
        tty = sys.stdout.isatty()
        frames = "|/-\\"
        def animate():
            index = 0
            while not stop.wait(0.12):
                if tty:
                    sys.stdout.write(f"\rIA processando {frames[index % len(frames)]}")
                    sys.stdout.flush()
                    index += 1
        sys.stdout.write("IA: iniciando planejamento/análise" + ("\n" if not tty else ""))
        sys.stdout.flush()
        thread = threading.Thread(target=animate, daemon=True)
        thread.start()
        try:
            result = self._provider.complete(system, user, images)
        except Exception:
            stop.set(); thread.join()
            if tty:
                sys.stdout.write("\rIA: chamada falhou                  \n")
            else:
                sys.stdout.write("IA: chamada falhou\n")
            sys.stdout.flush()
            raise
        stop.set(); thread.join()
        elapsed = time.monotonic() - started
        label = getattr(self._provider, "last_used_model", self._provider.label)
        if tty:
            sys.stdout.write(f"\rIA: resposta recebida de {label} em {elapsed:.1f}s\n")
        else:
            sys.stdout.write(f"IA: resposta recebida de {label} em {elapsed:.1f}s\n")
        sys.stdout.flush()
        return result






def print_metrics(metrics: dict):
    overall = f"{metrics['accuracy']:.0%}" if metrics["accuracy"] is not None else "sem respostas"
    print(f"\nResultado: {metrics['correct']}/{metrics['answered']} acertos ({overall})")
    print("\nMatéria                 Tentativas  Acertos  Desempenho  Situação")
    for subject, row in metrics["subjects"].items():
        accuracy = f"{row['accuracy']:.0%}" if row["accuracy"] is not None else "—"
        print(f"{subject:<23} {row['attempts']:>10}  {row['correct']:>7}  {accuracy:>10}  {row['status']}")
        reasons = ", ".join(f"{k}={v}" for k, v in row["error_reasons"].items() if v)
        if reasons:
            print(f"  motivos: {reasons}")


def print_analysis(result: dict):
    print_metrics(result["metrics"])
    if result.get("analysis"):
        print("\n" + result["analysis"]["summary"])
        for index, step in enumerate(result["analysis"]["steps"], 1):
            print(f"\n{index}. {step['subject']} — {step['action']}")
            print(f"   Por quê: {step['rationale']}")
            print(f"   Critério de saída: {step['exit_criterion']}")
    else:
        print("\nA IA não produziu uma análise válida após uma tentativa de correção.")
        print("As métricas foram preservadas. Erro: " + str(result.get("analysis_error")))


def add_llm_args(parser):
    parser.add_argument("--provider", default="ollama", help="provedor já suportado pelo classifier.py")
    parser.add_argument("--model", help="modelo; Gemini aceita lista separada por vírgulas")
    parser.add_argument("--base-url", help="URL do provedor; Ollama aceita múltiplos hosts")
    parser.add_argument("--api-key-env", help="nome da variável de ambiente da chave")
    parser.add_argument("--workers", type=int, default=1,
                        help="limite de workers; o diagnóstico faz uma chamada de IA por etapa")
    parser.add_argument("--ollama-threads", type=int, default=0,
                        help="threads de CPU por inferência Ollama; 0 deixa o Ollama escolher")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)

    p_import = subs.add_parser("importar-gabarito", help="associa um gabarito PDF/JSON a um simulado")
    p_import.add_argument("--gabarito", type=Path, required=True)
    p_import.add_argument("--questoes", type=Path, required=True, help="questoes.classificado.json correspondente")
    p_import.add_argument("--db", type=Path, default=DEFAULT_DB)
    p_import.add_argument("--replace", action="store_true", help="substitui respostas já importadas em conflito")
    p_import.add_argument("--versao", help="versão do caderno no gabarito (ex.: V1 ou S1)")

    p_generate = subs.add_parser("gerar", help="gera e persiste um simulado diagnóstico")
    p_generate.add_argument("--curso", default="eng-comp-sao-carlos")
    p_generate.add_argument("--modo", choices=("adaptive", "static"), default="adaptive",
                            help="adaptive (padrão): uma questão por vez, escolhida pelas respostas; "
                                 "static: sorteio completo de uma vez")
    p_generate.add_argument("--quantidade", type=int,
                            help="orçamento de questões (padrão: 30 no adaptativo, 51 no estático)")
    p_generate.add_argument("--seed", type=int, help="semente para reproduzir o sorteio")
    p_generate.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    p_generate.add_argument("--db", type=Path, default=DEFAULT_DB)
    p_generate.add_argument("--questoes-dir", type=Path, default=DEFAULT_QUESTIONS)
    add_llm_args(p_generate)

    p_answer = subs.add_parser("responder", help="responde ou retoma um diagnóstico interativo")
    p_answer.add_argument("id")
    p_answer.add_argument("--db", type=Path, default=DEFAULT_DB)
    add_llm_args(p_answer)

    p_result = subs.add_parser("resultado", help="exibe métricas e plano de estudo")
    p_result.add_argument("id")
    p_result.add_argument("--db", type=Path, default=DEFAULT_DB)

    p_list = subs.add_parser("listar", help="lista tentativas diagnósticas")
    p_list.add_argument("--db", type=Path, default=DEFAULT_DB)

    args = parser.parse_args(argv)
    try:
        if hasattr(args, "workers") and args.workers < 1:
            raise ValueError("--workers deve ser pelo menos 1")
        if hasattr(args, "ollama_threads") and args.ollama_threads < 0:
            raise ValueError("--ollama-threads não pode ser negativo")
        db = core.connect_db(args.db)
        try:
            if args.command == "importar-gabarito":
                summary = import_key(db, args.gabarito.resolve(), args.questoes.resolve(), args.replace, args.versao)
                print(f"Gabarito associado ao simulado {summary['source_id']}: {summary['imported']} respostas.")
                if summary["questions_without_key"]:
                    print("Sem resposta importada: " + ", ".join(f"Q{n:02d}" for n in summary["questions_without_key"]))
                return 0

            if args.command == "gerar":
                if args.quantidade is None:
                    args.quantidade = (core.DEFAULT_ADAPTIVE_TOTAL if args.modo == "adaptive"
                                       else core.DEFAULT_STATIC_TOTAL)
                if args.quantidade < 1:
                    raise ValueError("--quantidade deve ser positiva")
                config = core.load_course_config(args.config.resolve())
                profile = core.course_profile(config, args.curso)
                pool, excluded = core.load_question_pool(args.questoes_dir.resolve(), db, list(profile["pesos"]))
                if not pool:
                    raise ValueError("nenhuma questão elegível encontrada em " + str(args.questoes_dir.resolve())
                                     + f"; exclusões: {excluded}. Importe gabaritos e confira --questoes-dir")
                seed = args.seed if args.seed is not None else random.SystemRandom().randrange(0, 2**63)
                provider = build_provider(args)
                if args.workers > 1:
                    print("Nota: o planejamento do diagnóstico é uma chamada única de IA; workers não paralelizam essa etapa.")
                print(f"Configuração IA: {args.provider} · workers={args.workers} · threads Ollama={args.ollama_threads}")
                attempt = core.create_attempt(db, config, args.curso, pool, total=args.quantidade,
                                              seed=seed, provider=provider, excluded=excluded, mode=args.modo)
                print(f"Diagnóstico {attempt['id']} ({attempt['mode']}) criado para {attempt['course']}.")
                if attempt["mode"] == "adaptive":
                    budget = attempt["budget"]
                    print(f"Até {budget['total']} questões, uma por vez · seed {attempt['seed']}.")
                    for subject in profile["pesos"]:
                        planned = budget["by_subject"].get(subject, 0)
                        available_count = attempt["distribution"]["available_by_subject"].get(subject, 0)
                        weight = attempt["weights"].get(subject, 0)
                        if planned:
                            print(f"  {subject}: até {planned} questão(ões) · peso {weight:.0%} · "
                                  f"{available_count} elegível(is)")
                        else:
                            print(f"  {subject}: fora do orçamento (peso {weight:.0%})")
                else:
                    print(f"Questões: {attempt['selected']}/{attempt['requested']} · seed {attempt['seed']}.")
                    if attempt["planner"] and attempt["planner"]["status"] != "llm":
                        print("Planejamento temático: fallback determinístico (" + str(attempt["planner"].get("error")) + ")")
                    for subject in profile["pesos"]:
                        selected_count = attempt["distribution"]["selected_by_subject"].get(subject, 0)
                        available_count = attempt["distribution"]["available_by_subject"].get(subject, 0)
                        print(f"  {subject}: {selected_count} selecionada(s) / {available_count} elegível(is)")
                    if attempt["selected"] < attempt["requested"]:
                        print("Aviso: banco elegível insuficiente; o diagnóstico foi criado com a cobertura disponível.")
                for subject in attempt["distribution"]["unavailable_subjects"]:
                    print(f"Aviso: sem questão com gabarito para {subject}; matéria ficará sem dados.")
                print(f"Para começar: python diagnostico.py responder {attempt['id']}")
                return 0

            if args.command == "responder":
                attempt = core.get_attempt(db, args.id)
                pending = core.list_questions_for_attempt(db, args.id, unanswered_only=True)
                if not pending:
                    if attempt["metrics"]:
                        print_analysis({"metrics": attempt["metrics"], "analysis": attempt["analysis"],
                                        "analysis_error": attempt["analysis_error"]})
                        return 0
                    provider = build_provider(args)
                    print_analysis(core.finalize_attempt(db, args.id, provider))
                    return 0
                adaptive = attempt.get("mode") == "adaptive"
                while pending:
                    row = pending[0]
                    question = json.loads(row["question_json"])
                    of = f"até {attempt['requested_count']}" if adaptive else str(attempt["actual_count"])
                    print(f"\nQuestão {row['ordinal']}/{of} · "
                          f"prova {row['source_id']} Q{row['question_number']:02d} · {row['subject']} · {row['topic']}")
                    if question.get("_texto_compartilhado"):
                        print("Texto-base:\n" + question["_texto_compartilhado"])
                    if question.get("enunciado"):
                        print(question["enunciado"])
                    if question.get("nota"):
                        print("Nota: " + question["nota"])
                    for letter, alternative in sorted((question.get("alternativas") or {}).items()):
                        text = alternative.get("texto") or "[alternativa apresentada como figura]"
                        print(f"  ({letter}) {text}")
                    if question.get("imagens_completas"):
                        base = Path(question.get("_source_dir", "."))
                        paths = [str((base / image).resolve()) for image in question["imagens_completas"]]
                        print("Figuras extraídas: " + ", ".join(paths))
                    valid = set(question.get("alternativas", {}))
                    while True:
                        raw = input("Sua resposta (A-E ou Enter para branco): ").strip().upper()
                        if not raw or raw in valid:
                            break
                        print("Escolha uma alternativa exibida ou deixe em branco.")
                    if not raw:
                        progress = core.record_response(db, args.id, row["ordinal"], None)
                        print("Resposta em branco registrada.")
                    elif raw == row["correct_answer"]:
                        progress = core.record_response(db, args.id, row["ordinal"], raw)
                        print("Resposta registrada.")
                    else:
                        print("Resposta registrada. Qual foi o motivo principal do erro?")
                        choices = {"1": "unknown_content", "2": "calc_error", "3": "misread"}
                        print("  1) unknown_content — não sabia o conteúdo")
                        print("  2) calc_error — sabia, mas errou a conta")
                        print("  3) misread — sabia, mas não interpretou corretamente")
                        while True:
                            reason = input("Motivo [1-3]: ").strip()
                            if reason in choices:
                                break
                            print("Informe 1, 2 ou 3.")
                        progress = core.record_response(db, args.id, row["ordinal"], raw, choices[reason])
                    if progress.get("done") and progress.get("stop_reason") == "converged":
                        print("\nDiagnóstico adaptativo concluído: as áreas de maior peso já têm evidência suficiente.")
                    pending = core.list_questions_for_attempt(db, args.id, unanswered_only=True)
                provider = build_provider(args)
                print_analysis(core.finalize_attempt(db, args.id, provider))
                return 0

            if args.command == "resultado":
                attempt = core.get_attempt(db, args.id)
                if attempt["metrics"]:
                    print_analysis({"metrics": attempt["metrics"], "analysis": attempt["analysis"],
                                    "analysis_error": attempt["analysis_error"]})
                else:
                    print(f"Diagnóstico {args.id}: {attempt['status']} · "
                          f"{attempt['actual_count']} questões. Retome com `responder {args.id}`.")
                return 0

            if args.command == "listar":
                rows = db.execute("SELECT id,course_name,status,actual_count,created_at FROM attempts ORDER BY created_at DESC").fetchall()
                if not rows:
                    print("Nenhum diagnóstico salvo.")
                for row in rows:
                    print(f"{row['id']} · {row['course_name']} · {row['status']} · {row['actual_count']} questões · {row['created_at']}")
                return 0
        finally:
            db.close()
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
