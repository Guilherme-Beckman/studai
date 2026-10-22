"""
Medição das provas antigas do acervo FUVEST: extração e gabarito por prova.

    python benchmark.py legacy-exams [--first-year 1996] [--last-year 2021] [--out docs/benchmarks/legacy-exams.md]

Para cada PDF de prova da 1ª fase: questões achadas e faltantes, sinais de revisão
(`revisar`), versão detectada e quantas questões têm resposta no gabarito (PDF do
manifesto, página de gabarito no fim da prova ou respostas impressas por questão).
Não grava nada no banco nem no acervo; as extrações vão para um diretório temporário.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import tempfile
import time
from pathlib import Path

import pymupdf

from studai.features.answer_keys.service import (
    answer_key_pages, answer_key_versions, detect_exam_variant, parse_answer_key_pdf, printed_answer_key,
)
from studai.features.questions import ocr as ocr_mod
from studai.features.questions.extractor import extract_pdf
from studai.settings import SERVICE_ROOT

DAY_RE = re.compile(r"dia[_ -]?(\d)", re.I)
KEY_DAY_RE = re.compile(r"gab[_ -]?(\d)$", re.I)
FLAGS = ("enunciado_vazio", "alternativas_incompletas", "alternativa_vazia", "formula_matematica", "glifo_estranho")


def _exam_pdfs(root: Path, first: int, last: int) -> list[Path]:
    return sorted(p for p in root.glob("*/1a_fase/provas/fu*.pdf")
                  if p.parts[-4].isdigit() and first <= int(p.parts[-4]) <= last)


def _key_for(exam: Path) -> Path | None:
    keys = sorted((exam.parent.parent / "gabaritos").glob("*.pdf"), key=lambda k: ("_cor" in k.stem, k.name))
    day = DAY_RE.search(exam.stem)
    if day:
        keys = [k for k in keys if (m := KEY_DAY_RE.search(k.stem)) and m.group(1) == day.group(1)]
    return keys[0] if keys else None


def measure(exam: Path, workdir: Path, ocr) -> dict:
    started = time.time()
    row: dict = {"year": int(exam.parts[-4]), "exam": exam.name}
    try:
        data = json.loads(extract_pdf(exam, workdir / exam.stem, dpi=100, full_crops=False, ocr=ocr)
                          .read_text(encoding="utf-8"))
    except Exception as exc:                      # medição: registra e segue para a próxima prova
        row["error"] = f"extração: {type(exc).__name__}: {exc}"
        return row
    questions = data.get("questoes", [])
    numbers = sorted(int(q["numero"]) for q in questions)
    flags = collections.Counter(f for q in questions for f in q.get("revisar", []))
    version = detect_exam_variant(exam, ocr=ocr)
    key, source, answers = _key_for(exam), "", {}
    try:
        if key:
            answers, source = parse_answer_key_pdf(key, version), key.name
        else:
            with pymupdf.open(exam) as doc:
                pages = answer_key_pages(doc)
            if pages:
                versions = answer_key_versions(exam, pages)
                version = versions[0] if len(versions) == 1 else version
                answers, source = parse_answer_key_pdf(exam, version, pages=pages), "impresso no fim da prova"
            elif printed := printed_answer_key(data):
                answers, source = printed, "impresso em cada questão"
    except ValueError as exc:
        source = f"erro: {exc}"
    expected = max([len(answers), *numbers]) if (answers or numbers) else 0
    row.update({
        "version": version, "questions": len(numbers), "expected": expected,
        "missing": [n for n in range(1, expected + 1) if n not in set(numbers)],
        "flags": {f: flags.get(f, 0) for f in FLAGS},
        "answer_key": source or "ausente", "answers": len(answers),
        "keyed_questions": sum(n in answers for n in numbers), "seconds": round(time.time() - started, 1),
    })
    return row


def to_markdown(rows: list[dict]) -> str:
    head = ("| Ano | Prova | Versão | Questões | Faltando | Enunciado vazio | Alt. incompletas | Alt. vazia "
            "| Fórmula | Glifo | Gabarito | Com resposta |\n|" + "---|" * 12)
    lines = [head]
    for r in rows:
        if "error" in r:
            lines.append(f"| {r['year']} | {r['exam']} | — | {r['error']} |" + " |" * 8)
            continue
        f = r["flags"]
        missing = ", ".join(map(str, r["missing"][:6])) + ("…" if len(r["missing"]) > 6 else "")
        lines.append(f"| {r['year']} | {r['exam']} | {r['version'] or '?'} | {r['questions']}/{r['expected']} "
                     f"| {missing or '—'} | {f['enunciado_vazio']} | {f['alternativas_incompletas']} "
                     f"| {f['alternativa_vazia']} | {f['formula_matematica']} | {f['glifo_estranho']} "
                     f"| {r['answer_key']} | {r['keyed_questions']}/{r['questions']} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> list[dict]:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--archive", type=Path, default=SERVICE_ROOT / "fuvest_acervo")
    parser.add_argument("--first-year", type=int, default=1996)
    parser.add_argument("--last-year", type=int, default=2021)
    parser.add_argument("--out", type=Path, help="grava a tabela em Markdown (e o JSON ao lado)")
    args = parser.parse_args(argv)
    engine = ocr_mod.default_engine()
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        for exam in _exam_pdfs(args.archive, args.first_year, args.last_year):
            rows.append(measure(exam, Path(tmp), engine))
            print(json.dumps(rows[-1], ensure_ascii=False), flush=True)
    table = to_markdown(rows)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(table, encoding="utf-8")
        args.out.with_suffix(".json").write_text(json.dumps(rows, ensure_ascii=False, indent=1) + "\n",
                                                 encoding="utf-8")
    else:
        print(table)
    return rows


if __name__ == "__main__":
    main()
