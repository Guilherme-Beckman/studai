"""Extrai a amostra FUVEST 2017–2026 (1ª fase, uma versão por ano) para revisão local."""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from studai.features.answer_keys.service import import_answer_key, parse_answer_key_pdf
from studai.features.diagnostics.service import connect_db
from studai.features.questions.extractor import extract_pdf
from studai.integrations.ocr import default_ocr
from studai.settings import DEFAULT_DIAGNOSTICS_DB, SERVICE_ROOT

FIRST_YEAR, LAST_YEAR = 2017, 2026


def _manifest_items(root: Path, year: int) -> list[dict]:
    path = root / str(year) / "manifest.json"
    if not path.is_file():
        raise FileNotFoundError(f"manifesto ausente: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("itens")
    if not isinstance(items, list):
        raise ValueError(f"manifesto inválido: {path}")
    return [item for item in items if item.get("status") in {"baixado", "ja_existia"}]


def select_exam_and_key(root: Path, year: int) -> tuple[Path, Path, str]:
    items = _manifest_items(root, year)
    exams = [item for item in items if item.get("fase") == "1a_fase"
             and item.get("tipo") == "provas" and item.get("arquivo")
             and "local" not in item.get("texto", "").casefold()]
    if not exams:
        raise ValueError(f"nenhuma prova da 1ª fase no manifesto de {year}")
    chosen = [item for item in exams if re.search(r"\b(?:V1|V)\b", item.get("texto", ""), re.I)]
    if len(chosen) == 1:
        exam_item = chosen[0]
    elif len(exams) == 1:
        exam_item = exams[0]
    else:
        raise ValueError(f"não foi possível escolher uma versão V única em {year}: "
                         + ", ".join(item.get("texto", "?") for item in exams))
    keys = [item for item in items if item.get("fase") == "1a_fase"
            and item.get("tipo") == "gabaritos" and item.get("arquivo")
            and "correspond" not in item.get("texto", "").casefold()]
    if not keys:
        raise ValueError(f"gabarito oficial da 1ª fase ausente em {year}")
    key_item = keys[0]
    def source_path(relative: str) -> Path:
        value = Path(relative)
        # O scraper grava caminhos a partir da raiz do projeto (fuvest_acervo/...),
        # enquanto este comando recebe a própria pasta fuvest_acervo como --archive.
        if value.parts and value.parts[0] == root.name:
            return root.parent / value
        return root / value
    exam, key = source_path(exam_item["arquivo"]), source_path(key_item["arquivo"])
    if not exam.is_file() or not key.is_file():
        raise FileNotFoundError(f"arquivo do manifesto não existe: {exam if not exam.is_file() else key}")
    is_v1 = (re.search(r"\bV1\b", exam_item.get("texto", ""), re.I)
             or re.search(r"(?:^|[^A-Za-z0-9])V1(?:$|[^A-Za-z0-9])", exam.stem, re.I))
    version = "V1" if is_v1 else "V"
    return exam, key, version


def _review_row(question: dict, *, year: int, version: str, exam_id: str,
                source_pdf: str, answer: str | None) -> dict:
    shared_text = str(question.get("texto_compartilhado", "") or "")
    text = "\n".join(part for part in (shared_text, question.get("enunciado", ""), question.get("nota", "")) if part)
    return {
        "id": f"fuvest-{year}-1a-{version}-q{int(question['numero']):02d}",
        "year": year, "year_version": str(year), "phase": "1a_fase", "version": version,
        "exam": exam_id, "source": "FUVEST oficial", "source_pdf": source_pdf,
        "question": text, "alternatives": question.get("alternativas", {}),
        "images": question.get("figuras_enunciado", []), "answer_key": answer,
        "is_annulled": answer == "*", "subject": "", "topic": "",
        "reviewed": False, "review_status": "pending",
        "review_reasons": list(question.get("revisar", [])),
    }


def process_archive(*, root: Path, output: Path, report_path: Path,
                    dataset_path: Path, db_path: Path, review_csv_path: Path | None = None,
                    dpi: int = 160) -> dict:
    """Processa manifestos e escreve extrações/revisão apenas no diretório de saída."""
    root, output = root.resolve(), output.resolve()
    report = {"range": [FIRST_YEAR, LAST_YEAR], "phase": "1a_fase", "created_at": datetime.now(timezone.utc).isoformat(),
              "selected_exams": [], "totals": {"exams": 0, "questions": 0, "annulled": 0, "pending_review": 0},
              "rag": {"indexed_examples": 0, "status": "aguardando_revisao_humana"},
              "errors": [], "limitations": [
                  "2017 e 2018: o extrator não detecta os marcadores de todas as questões; importação recusada.",
                  "2021: o extrator não encontrou a questão 90; importação recusada.",
                  "Gabaritos de correspondência de 2017–2018 não substituem o gabarito direto da versão V.",
              ]}
    review_rows = []
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = connect_db(db_path)
    ocr = default_ocr()      # 2021 só tem texto em bitmap
    try:
        for year in range(FIRST_YEAR, LAST_YEAR + 1):
            try:
                exam, key, version = select_exam_and_key(root, year)
                exam_id = f"fuvest-{year}-1a-{version}"
                destination = output / str(year) / exam.stem
                questions_path = extract_pdf(exam, destination, dpi=dpi, full_crops=False, ocr=ocr)
                data = json.loads(questions_path.read_text(encoding="utf-8"))
                if int(data.get("total_questoes", 0)) != 90:
                    raise ValueError(f"extração incompleta: {data.get('total_questoes', 0)}/90 questões; "
                                     "gabarito não associado para evitar desalinhamento")
                data.update({"prova": exam_id, "ano": year, "fase": "1a_fase", "versao": version,
                             "arquivo_origem": str(exam.relative_to(root)),
                             "gabarito_origem": str(key.relative_to(root))})
                questions_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                answers = parse_answer_key_pdf(key, version)
                imported = import_answer_key(db, key, questions_path, replace=True, variant=version,
                                             association_method="manifest_fuvest")
                for question in data.get("questoes", []):
                    number = int(question["numero"])
                    answer = answers.get(number)
                    question["resposta"] = answer
                    question["gabarito_anulado"] = answer == "*"
                    shared = data.get("textos_compartilhados", {}).get(question.get("texto_id"), {})
                    question["texto_compartilhado"] = shared.get("texto", "")
                    review_rows.append(_review_row(question, year=year, version=version, exam_id=exam_id,
                                                   source_pdf=str(exam.relative_to(root)), answer=answer))
                questions_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                annulled = sum(value == "*" for value in answers.values())
                exam_result = {"year": year, "version": version, "exam_pdf": str(exam.relative_to(root)),
                               "answer_key_pdf": str(key.relative_to(root)), "questions_json": str(questions_path),
                               "extracted_questions": data.get("total_questoes", 0), "answers_imported": imported["imported"],
                               "unkeyed_questions": imported["unkeyed_questions"], "annulled_items": annulled,
                               "questions_flagged_for_review": sum(bool(q.get("revisar")) for q in data.get("questoes", []))}
                report["selected_exams"].append(exam_result)
                report["totals"]["exams"] += 1
                report["totals"]["questions"] += int(data.get("total_questoes", 0))
                report["totals"]["annulled"] += annulled
            except Exception as exc:
                report["errors"].append({"year": year, "error": f"{type(exc).__name__}: {exc}"})
    finally:
        db.close()
    report["totals"]["pending_review"] = len(review_rows)
    report["review_dataset"] = str(dataset_path.resolve())
    if review_csv_path:
        from studai.tools.review_edubench import export_rows
        export_rows(review_rows, review_csv_path, limit=max(1, len(review_rows)))
        report["review_csv"] = str(review_csv_path.resolve())
    report["output_directory"] = str(output)
    report["diagnostics_database"] = str(db_path.resolve())
    report_path.parent.mkdir(parents=True, exist_ok=True)
    dataset_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    dataset_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in review_rows), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=SERVICE_ROOT / "fuvest_acervo")
    parser.add_argument("--output", type=Path, default=SERVICE_ROOT / "simulados/processados/fuvest-2017-2026")
    parser.add_argument("--report", type=Path, default=SERVICE_ROOT / "simulados/cache/fuvest-2017-2026-report.json")
    parser.add_argument("--dataset", type=Path, default=SERVICE_ROOT / "simulados/cache/fuvest-2017-2026-review.jsonl")
    parser.add_argument("--review-csv", type=Path, default=SERVICE_ROOT / "simulados/cache/fuvest-2017-2026-review.csv")
    parser.add_argument("--diagnostics-db", type=Path, default=DEFAULT_DIAGNOSTICS_DB)
    parser.add_argument("--dpi", type=int, default=160)
    args = parser.parse_args()
    if args.dpi < 72 or args.dpi > 600:
        parser.error("--dpi deve estar entre 72 e 600")
    result = process_archive(root=args.archive, output=args.output, report_path=args.report,
                             dataset_path=args.dataset, db_path=args.diagnostics_db,
                             review_csv_path=args.review_csv, dpi=args.dpi)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
