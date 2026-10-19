"""Fixtures sintéticas (provas revisadas + gabaritos) para os testes de relatórios."""
import json
from datetime import datetime, timezone
from pathlib import Path

from studai.features.classification.taxonomy import CONTENT_TAXONOMY_VERSION
from studai.features.diagnostics import service as diagnostics

COURSE_ID = "eng-comp-sao-carlos"
CONTENTS = {
    "Matemática": ["matematica.conjuntos-numericos-e-operacoes.operacoes-com-conjuntos",
                   "matematica.conjuntos-numericos-e-operacoes.irracionais-e-reais",
                   "matematica.divisibilidade-e-numeros-primos.criterios-de-divisibilidade"],
    "Física": ["fisica.atrito-e-plano-inclinado.atrito-estatico-e-cinetico",
               "fisica.atrito-e-plano-inclinado.plano-inclinado-com-atrito",
               "fisica.cinematica-escalar.movimento-uniforme"],
}
EXAMS = ("fuvest2024_primeira_fase_prova_K-test0001", "fuvest2025_primeira_fase_prova_V1-test0002")
PER_SUBJECT = 10
LETTERS = "ABCDE"


def config() -> dict:
    profile = {"nome": "Engenharia de Computação (teste)", "pesos": {"Matemática": 3, "Física": 2},
               "disciplinas_especificas": ["Matemática", "Física"]}
    return {"version": 2, "cursos": {COURSE_ID: profile}}


def build(root: Path) -> tuple[Path, Path, Path]:
    """Cria ``processados/``, ``cursos.json`` e o banco com gabaritos. Retorna os três caminhos."""
    processed = root / "processados"
    db_path = root / "historico.sqlite3"
    config_path = root / "cursos.json"
    config_path.write_text(json.dumps(config(), ensure_ascii=False), encoding="utf-8")
    db = diagnostics.connect_db(db_path)
    try:
        for exam_index, source_id in enumerate(EXAMS):
            exam_dir = processed / "web" / source_id
            exam_dir.mkdir(parents=True)
            questions, reviews, number = [], {}, 0
            for subject, contents in CONTENTS.items():
                for i in range(PER_SUBJECT):
                    number += 1
                    content_id = contents[i % len(contents)]
                    questions.append({
                        "numero": number, "enunciado": f"Questão {number}", "materia": subject,
                        "alternativas": {letter: {"texto": letter.lower()} for letter in LETTERS},
                        "classificacao": {"materia": subject, "assunto": "x", "conteudo_principal_id": content_id},
                        "revisar": []})
                    # Revisadas por humano: o pool do diagnóstico só aceita questões revisadas.
                    reviews[str(number)] = {"status": "reviewed", "subject": subject, "content_id": content_id,
                                            "taxonomy_version": CONTENT_TAXONOMY_VERSION}
                    db.execute("INSERT INTO answer_keys(source_id,question_number,answer,source_pdf,imported_at) "
                               "VALUES(?,?,?,?,?)", (source_id, number, LETTERS[(number + exam_index) % 5],
                                                     "gabarito.pdf", datetime.now(timezone.utc).isoformat()))
            data = {"prova": source_id, "arquivo_origem": f"{source_id.rsplit('-', 1)[0]}.pdf", "questoes": questions}
            (exam_dir / "questoes.classificado.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            (exam_dir / "revisao.json").write_text(json.dumps({"schema_version": 1, "questions": reviews}),
                                                   encoding="utf-8")
        db.commit()
    finally:
        db.close()
    return processed, config_path, db_path


def load_pool(db, processed: Path) -> list[dict]:
    pool, _ = diagnostics.load_question_pool(processed, db, list(config()["cursos"][COURSE_ID]["pesos"]))
    return pool
