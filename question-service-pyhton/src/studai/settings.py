"""Caminhos compartilhados do serviço, configuráveis nas interfaces de execução."""
from pathlib import Path

# Durante a transição, dados e arquivos de entrada permanecem no diretório legado.
SERVICE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_QUESTIONS_DIR = SERVICE_ROOT / "simulados" / "processados"
DEFAULT_COURSE_CONFIG = SERVICE_ROOT / "diagnostico" / "cursos.json"
DEFAULT_DIAGNOSTICS_DB = SERVICE_ROOT / "diagnostico" / "historico.sqlite3"
DEFAULT_ANSWER_KEYS_DIR = SERVICE_ROOT / "docs" / "gabaritos"

