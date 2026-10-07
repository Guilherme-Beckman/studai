#!/usr/bin/env python3
"""Extrai e classifica todos os PDFs de uma pasta, mantendo uma saída por simulado.

Uso:
    python processar_simulados.py [pasta_com_pdfs]
    python processar_simulados.py simulados/ --base-url http://localhost:11434,http://192.168.1.50:11434

As saídas ficam em <pasta_com_pdfs>/processados/<nome-do-pdf>/.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def main() -> int:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pasta", nargs="?", type=Path, default=here / "simulados",
                    help="pasta com os PDFs (default: ./simulados)")
    ap.add_argument("--output-dir", type=Path, help="pasta das saídas (default: <pasta>/processados)")
    ap.add_argument("--provider", default="ollama")
    ap.add_argument("--model", help="modelo usado pelo provedor")
    ap.add_argument("--base-url", help="URL da API; Ollama aceita vários hosts separados por vírgula")
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

        classify_cmd = [sys.executable, str(classifier_script), str(destination / "questoes.json"),
                        "--provider", args.provider, "--imagens", args.imagens]
        if args.model:
            classify_cmd.extend(["--model", args.model])
        if args.base_url:
            classify_cmd.extend(["--base-url", args.base_url])
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
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
