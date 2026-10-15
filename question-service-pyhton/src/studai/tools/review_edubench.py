"""Exporta candidatos EduBench para revisão em planilha e importa rótulos revisados."""
from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

from studai.features.classification.service import DEFAULT_TAXONOMY, DEFAULT_TOPIC_TAXONOMY
from studai.features.classification.taxonomy import CONTENT_INDEX, CONTENT_TAXONOMY_VERSION
from studai.settings import DEFAULT_DIAGNOSTICS_DB, DEFAULT_QUESTIONS_DIR, SERVICE_ROOT


REVIEW_COLUMNS = ("id", "exam", "year", "year_version", "question", "supporting_texts", "alternatives", "images",
                  "suggested_subject", "suggested_topic", "subject", "topic", "content_id", "content_path",
                  "taxonomy_version", "reference_answer", "reference_short_answer",
                  "grading_guidelines", "source", "source_pdf", "phase", "version", "answer_key", "is_annulled")


def sample_by_year(rows: list[dict], limit: int, seed: int = 2026) -> list[dict]:
    """Seleciona amostra determinística distribuída pelos anos disponíveis."""
    groups: dict[int, list[dict]] = defaultdict(list)
    for row in rows:
        groups[int(row.get("year") or 0)].append(row)
    rng = random.Random(seed)
    for group in groups.values():
        rng.shuffle(group)
    result = []
    years = sorted(groups)
    while len(result) < limit and years:
        remaining = []
        for year in years:
            if groups[year] and len(result) < limit:
                result.append(groups[year].pop())
            if groups[year]:
                remaining.append(year)
        years = remaining
    return result


def export_review(candidates: Path, output: Path, *, limit: int = 100, seed: int = 2026) -> int:
    rows = [json.loads(line) for line in candidates.read_text(encoding="utf-8").splitlines() if line.strip()]
    return export_rows(rows, output, limit=limit, seed=seed)


def export_rows(rows: list[dict], output: Path, *, limit: int = 100, seed: int = 2026) -> int:
    selected = sample_by_year(rows, limit, seed)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=REVIEW_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in selected:
            values = {key: row.get(key, "") for key in REVIEW_COLUMNS}
            for key in ("alternatives", "images"):
                if not isinstance(values[key], str):
                    values[key] = json.dumps(values[key], ensure_ascii=False)
            writer.writerow(values)
    return len(selected)


def processed_exam_rows(root: Path, diagnostics_db: Path = DEFAULT_DIAGNOSTICS_DB) -> list[dict]:
    """Prepara candidatos dos simulados locais, preservando identidade e gabaritos importados."""
    from studai.features.answer_keys.service import exam_variant, source_id_for

    answer_keys: dict[tuple[str, int], tuple[str, bool]] = {}
    if diagnostics_db.is_file():
        try:
            connection = sqlite3.connect(f"file:{diagnostics_db.resolve()}?mode=ro", uri=True)
            try:
                exists = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='answer_keys'"
                ).fetchone()
                if exists:
                    answer_keys = {(row[0], int(row[1])): (row[2], bool(row[3])) for row in connection.execute(
                        "SELECT source_id,question_number,answer,is_annulled FROM answer_keys")}
            finally:
                connection.close()
        except sqlite3.Error:
            answer_keys = {}

    rows = []
    for path in sorted(Path(root).rglob("questoes.classificado.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        shared_all = data.get("textos_compartilhados", {})
        source_id = source_id_for(data, path)
        stem = Path(str(data.get("arquivo_origem") or path.parent.name)).stem
        year_match = re.search(r"20\d{2}", stem)
        phase_match = re.search(r"fase[-_ ]?(1|2)", stem, re.I)
        phase = f"{phase_match.group(1)}a_fase" if phase_match else ""
        version = exam_variant(Path(stem)) or ""
        exam = "Fuvest" if "fuvest" in stem.casefold() else "Simulado local"
        for question in data.get("questoes", []):
            classification = question.get("classificacao") or {}
            subject = question.get("materia") or classification.get("materia") or ""
            topic = classification.get("assunto") or ""
            number = question.get("numero")
            try:
                key, annulled = answer_keys.get((source_id, int(number)), ("", False))
            except (TypeError, ValueError):
                key, annulled = "", False
            shared = shared_all.get(question.get("texto_id") or "") or {}
            alternatives = {letter: value.get("texto", "") for letter, value in
                            (question.get("alternativas") or {}).items()}
            image_paths = list(question.get("figuras_enunciado") or []) + \
                list(question.get("imagens_completas") or [])
            if isinstance(shared, dict):
                image_paths += list(shared.get("figuras") or [])
            images = []
            for image_path in image_paths:
                try:
                    resolved = (path.parent / str(image_path)).resolve()
                    images.append(resolved.relative_to(SERVICE_ROOT.resolve()).as_posix())
                except (OSError, ValueError):
                    continue
            shared_text = shared.get("texto", "") if isinstance(shared, dict) else ""
            if question.get("nota"):
                shared_text = "\n\n".join(filter(None, [shared_text, question["nota"]]))
            rows.append({
                "id": f"{source_id}:q{number}", "exam": exam,
                "year": year_match.group(0) if year_match else "", "year_version": phase,
                "phase": phase, "version": version, "question": question.get("enunciado", ""),
                "supporting_texts": shared_text, "alternatives": alternatives, "images": images,
                "suggested_subject": subject, "suggested_topic": topic, "subject": "", "topic": "",
                "answer_key": key, "is_annulled": annulled,
                "source": data.get("arquivo_origem") or path.name,
                "source_pdf": data.get("arquivo_origem") or path.name,
                "review_status": "pending",
            })
    return rows


def import_review(review_csv: Path, output: Path) -> int:
    output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with review_csv.open("r", encoding="utf-8-sig", newline="") as source, \
            output.open("w", encoding="utf-8", newline="") as destination:
        reader = csv.DictReader(source)
        required = {"question", "subject"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError("CSV precisa conter as colunas question e subject")
        for line, row in enumerate(reader, 2):
            subject, topic = row.get("subject", "").strip(), row.get("topic", "").strip()
            topic = (row.get("topic_canonical") or topic).strip()
            content_id = str(row.get("content_id") or "").strip()
            if not subject or not (content_id or topic):
                raise ValueError(f"linha {line}: preencha subject e content_id (ou tópico legado) antes de importar")
            if subject not in DEFAULT_TAXONOMY:
                raise ValueError(f"linha {line}: subject deve ser uma matéria da taxonomia StudAI; recebido {subject!r}")
            if content_id:
                content = CONTENT_INDEX.get(content_id)
                if not content or not content["is_leaf"] or content["subject"] != subject:
                    raise ValueError(f"linha {line}: content_id {content_id!r} não é folha válida de {subject}")
                topic = content["name"]
                content_path = " → ".join(content["path"])
                taxonomy_version = CONTENT_TAXONOMY_VERSION
            else:
                if topic not in DEFAULT_TOPIC_TAXONOMY.get(subject, []):
                    raise ValueError(f"linha {line}: tópico {topic!r} não está na taxonomia de {subject}")
                content_path = ""
                taxonomy_version = "legacy-flat"
            item = {"id": row.get("id", ""), "exam": row.get("exam") or "USP",
                    "year": row.get("year", ""), "year_version": row.get("year_version", ""),
                    "question": row.get("question", "").strip(),
                    "supporting_texts": row.get("supporting_texts", "").strip(),
                    "alternatives": json.loads(row["alternatives"]) if row.get("alternatives", "").strip() else {},
                    "images": json.loads(row["images"]) if row.get("images", "").strip() else [],
                    "subject": subject, "topic": topic, "content_id": content_id,
                    "content_path": content_path, "taxonomy_version": taxonomy_version,
                    "suggested_subject": row.get("suggested_subject", "").strip(),
                    "suggested_topic": row.get("suggested_topic", "").strip(),
                    "reference_answer": row.get("reference_answer", "").strip(),
                    "reference_short_answer": row.get("reference_short_answer", "").strip(),
                    "grading_guidelines": row.get("grading_guidelines", "").strip(),
                    "phase": row.get("phase", "").strip(), "version": row.get("version", "").strip(),
                    "source_pdf": row.get("source_pdf", "").strip(),
                    "answer_key": row.get("answer_key", "").strip(),
                    "is_annulled": str(row.get("is_annulled", "")).strip().casefold() in ("1", "true", "sim", "yes"),
                    "source": row.get("source") or "recogna-nlp/EduBench", "reviewed": True}
            if not item["question"]:
                raise ValueError(f"linha {line}: enunciado vazio")
            destination.write(json.dumps(item, ensure_ascii=False) + "\n")
            count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    export_parser = modes.add_parser("export", help="gera CSV para revisão humana")
    export_parser.add_argument("candidates", type=Path)
    export_parser.add_argument("output", type=Path)
    export_parser.add_argument("--limit", type=int, default=100)
    export_parser.add_argument("--seed", type=int, default=2026)
    import_parser = modes.add_parser("import", help="valida CSV revisado e converte em JSONL de benchmark")
    import_parser.add_argument("review_csv", type=Path)
    import_parser.add_argument("output", type=Path)
    processed_parser = modes.add_parser("export-processed", help="gera CSV de revisão a partir das provas locais")
    processed_parser.add_argument("--root", type=Path, default=DEFAULT_QUESTIONS_DIR)
    processed_parser.add_argument("--diagnostico-db", type=Path, default=DEFAULT_DIAGNOSTICS_DB)
    processed_parser.add_argument("--output", type=Path,
                                  default=SERVICE_ROOT / "simulados" / "cache" / "fuvest-review.csv")
    processed_parser.add_argument("--limit", type=int, default=1000)
    processed_parser.add_argument("--seed", type=int, default=2026)
    args = parser.parse_args()
    if args.mode == "export":
        count = export_review(args.candidates, args.output, limit=max(1, args.limit), seed=args.seed)
        print(f"{count} questões distribuídas por ano exportadas para {args.output}")
    elif args.mode == "import":
        count = import_review(args.review_csv, args.output)
        print(f"{count} questões revisadas importadas para {args.output}")
    else:
        rows = processed_exam_rows(args.root, args.diagnostico_db)
        count = export_rows(rows, args.output, limit=max(1, args.limit), seed=args.seed)
        print(f"{count} questões locais exportadas para revisão em {args.output}")


if __name__ == "__main__":
    main()
