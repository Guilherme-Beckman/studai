#!/usr/bin/env python3
"""Extrai e classifica todos os PDFs de uma pasta, mantendo uma saída por simulado.

Uso:
    python processar_simulados.py [pasta_com_pdfs]
    python processar_simulados.py simulados/ --base-url http://localhost:11434,http://192.168.100.167:11434

As saídas ficam em <pasta_com_pdfs>/processados/<nome-do-pdf>/.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from studai.features.classification import service as clf
from studai.features.diagnostics import service as diag
from studai.settings import DEFAULT_ANSWER_KEYS_DIR, DEFAULT_DIAGNOSTICS_DB, SERVICE_ROOT


def main() -> int:
    here = SERVICE_ROOT
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pasta", nargs="?", type=Path, default=here / "simulados",
                    help="pasta com os PDFs (default: ./simulados)")
    ap.add_argument("--output-dir", type=Path, help="pasta das saídas (default: <pasta>/processados)")
    ap.add_argument("--gabaritos-dir", type=Path, default=DEFAULT_ANSWER_KEYS_DIR,
                    help="pasta monitorada para PDFs de gabarito")
    ap.add_argument("--diagnostico-db", type=Path, default=DEFAULT_DIAGNOSTICS_DB)
    ap.add_argument("--provider", default="ollama")
    ap.add_argument("--model", help="modelo usado pelo provedor")
    ap.add_argument("--base-url", help="URL da API; Ollama aceita vários hosts separados por vírgula")
    ap.add_argument("--api-key-env", help="variável de ambiente da chave, para provedores que exigem chave")
    ap.add_argument("--workers", type=int, help="questões processadas ao mesmo tempo")
    ap.add_argument("--ollama-threads", type=int, default=0)
    ap.add_argument("--imagens", choices=["nunca", "figuras", "sempre"], default="nunca")
    ap.add_argument("--dpi", type=int, default=200)
    ap.add_argument("--no-full-crops", action="store_true")
    args = ap.parse_args()

    source_dir = args.pasta.resolve()
    if not source_dir.is_dir():
        ap.error(f"pasta não encontrada: {source_dir}")
    output_root = (args.output_dir or source_dir / "processados").resolve()
    pdfs = sorted(p for p in source_dir.rglob("*")
                  if p.is_file() and p.suffix.lower() == ".pdf"
                  and output_root not in p.resolve().parents)
    if not pdfs:
        ap.error(f"nenhum PDF encontrado em {source_dir}")

    extract_script = here / "extract.py"
    classifier_script = here / "classifier.py"
    key_pdfs = sorted(p for p in args.gabaritos_dir.resolve().glob("*.pdf") if p.is_file())
    db = diag.connect_db(args.diagnostico_db.resolve())
    provider = None
    def get_provider():
        nonlocal provider
        if provider is None:
            clf.load_dotenv(here / ".env")
            model = args.model or ("qwen3.5:4b" if args.provider == "ollama" else
                                   "gemini-3.8-flash,gemini-3.7-flash,gemini-3.6-flash" if args.provider == "gemini" else "")
            provider = clf.make_provider(args.provider, model, base_url=args.base_url, key_env=args.api_key_env,
                                         temperature=0, max_tokens=1600, json_mode=True)
        return provider
    outcomes: list[tuple[Path, bool]] = []
    print(f"Encontrados {len(pdfs)} PDFs. Saídas em: {output_root}")
    for index, pdf in enumerate(pdfs, 1):
        relative = pdf.relative_to(source_dir)
        destination = output_root / relative.parent / pdf.stem
        print(f"\n[{index}/{len(pdfs)}] {relative}")
        extract_cmd = [sys.executable, str(extract_script), str(pdf), "--outdir", str(destination),
                       "--dpi", str(args.dpi)]
        if args.no_full_crops:
            extract_cmd.append("--no-full-crops")
        extracted = subprocess.run(extract_cmd, check=False)
        if extracted.returncode != 0:
            print(f"Falha na extração de {pdf.name}; seguindo para o próximo PDF.")
            outcomes.append((pdf, False))
            continue

        # Associa/importa o gabarito já na extração, antes da classificação.
        questions_json = destination / "questoes.json"
        selected_key, method = diag.associate_answer_key(pdf, key_pdfs)
        if selected_key is None and key_pdfs:
            try:
                selected_key, method = diag.associate_answer_key(pdf, key_pdfs, get_provider())
            except (Exception, SystemExit) as exc:
                print(f"Não foi possível iniciar LLM para associação: {exc}")
        if selected_key:
            try:
                variant = diag.exam_variant(pdf) or diag.exam_variant(selected_key)
                summary = diag.import_answer_key(db, selected_key, questions_json, replace=True,
                                                 variant=variant, association_method=method)
                # Imported count includes anuladas; report separately from persisted records.
                annulled = sum(1 for (answer,) in db.execute(
                    "SELECT answer FROM answer_keys WHERE source_id=? AND is_annulled=1",
                    (summary["source_id"],)))
                print(f"Gabarito associado ({method}): {selected_key.name} · versão {variant or 'única'} · "
                      f"{summary['imported']-annulled} respostas · {annulled} anulada(s).")
            except Exception as exc:
                print(f"Gabarito encontrado ({selected_key.name}), mas não foi importado: {exc}")
        else:
            print("Gabarito não associado com segurança; questões deste simulado não serão pontuáveis até revisão.")

        classify_cmd = [sys.executable, str(classifier_script), str(destination / "questoes.json"),
                        "--provider", args.provider, "--imagens", args.imagens]
        if args.model:
            classify_cmd.extend(["--model", args.model])
        if args.base_url:
            classify_cmd.extend(["--base-url", args.base_url])
        if args.api_key_env:
            classify_cmd.extend(["--api-key-env", args.api_key_env])
        if args.workers is not None:
            classify_cmd.extend(["--workers", str(args.workers)])
        if args.ollama_threads:
            classify_cmd.extend(["--ollama-threads", str(args.ollama_threads)])
        classified = subprocess.run(classify_cmd, check=False)
        outcomes.append((pdf, classified.returncode == 0))

    failed = [pdf for pdf, ok in outcomes if not ok]
    print(f"\nLote finalizado: {len(outcomes) - len(failed)}/{len(outcomes)} simulados concluídos.")
    if failed:
        print("Com falha: " + ", ".join(pdf.name for pdf in failed))
        db.close()
        return 1
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
