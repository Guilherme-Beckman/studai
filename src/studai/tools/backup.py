"""Backup periódico dos dados locais do StudAI (histórico, provas processadas e caches).

Cada execução gera um `studai-AAAAMMDD-HHMMSS.tar.gz` no destino. Bancos SQLite são
copiados pela API de backup do SQLite, então o arquivo fica consistente mesmo com a
aplicação gravando ao mesmo tempo. Arquivos auxiliares (-wal, -shm, -journal) ficam de fora.

    python -m studai.tools.backup --once
    python -m studai.tools.backup --interval-hours 24 --keep 7
"""
from __future__ import annotations

import argparse
import sqlite3
import tarfile
import tempfile
import time
from datetime import datetime
from pathlib import Path

from studai.settings import SERVICE_ROOT

DEFAULT_SOURCES = ("diagnostico", "simulados/processados", "simulados/uploads", "simulados/cache")
DEFAULT_DEST = SERVICE_ROOT / "backups"
ARCHIVE_PREFIX = "studai-"
ARCHIVE_SUFFIX = ".tar.gz"
_SQLITE_SIDECARS = ("-wal", "-shm", "-journal")


def _copy_sqlite(source: Path, target: Path) -> None:
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def create_backup(root: Path, sources: list[str], dest_dir: Path, now: datetime | None = None) -> Path:
    """Empacota `sources` (relativos a `root`) em um tar.gz dentro de `dest_dir`."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    archive = dest_dir / f"{ARCHIVE_PREFIX}{stamp}{ARCHIVE_SUFFIX}"
    partial = archive.with_name(archive.name + ".partial")
    with tempfile.TemporaryDirectory() as tmp, tarfile.open(partial, "w:gz") as tar:
        for rel in sources:
            base = root / rel
            if not base.exists():
                continue
            paths = [base] if base.is_file() else sorted(p for p in base.rglob("*") if p.is_file())
            for path in paths:
                if path.name.endswith(_SQLITE_SIDECARS):
                    continue
                arcname = path.relative_to(root).as_posix()
                if path.suffix == ".sqlite3":
                    snapshot = Path(tmp) / "snapshot.sqlite3"
                    snapshot.unlink(missing_ok=True)
                    _copy_sqlite(path, snapshot)
                    tar.add(snapshot, arcname=arcname)
                else:
                    tar.add(path, arcname=arcname)
    partial.replace(archive)
    return archive


def prune_backups(dest_dir: Path, keep: int) -> list[Path]:
    """Remove os backups mais antigos, mantendo os `keep` mais recentes."""
    archives = sorted(dest_dir.glob(f"{ARCHIVE_PREFIX}*{ARCHIVE_SUFFIX}"))
    removed = archives[:-keep] if keep > 0 else archives
    for path in removed:
        path.unlink()
    return removed


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=SERVICE_ROOT, help="raiz dos dados do serviço")
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST, help="diretório dos backups")
    parser.add_argument("--source", action="append", dest="sources",
                        help=f"caminho relativo à raiz (repetível; padrão: {', '.join(DEFAULT_SOURCES)})")
    parser.add_argument("--keep", type=int, default=7, help="quantos backups manter")
    parser.add_argument("--interval-hours", type=float, default=24.0, help="intervalo entre backups")
    parser.add_argument("--once", action="store_true", help="faz um backup e sai")
    args = parser.parse_args(argv)
    if args.keep < 1:
        parser.error("--keep deve ser pelo menos 1")
    sources = args.sources or list(DEFAULT_SOURCES)
    while True:
        archive = create_backup(args.root, sources, args.dest)
        removed = prune_backups(args.dest, args.keep)
        size_mb = archive.stat().st_size / 1_048_576
        print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] backup {archive.name} ({size_mb:.1f} MB); "
              f"removidos {len(removed)} antigos", flush=True)
        if args.once:
            return
        time.sleep(args.interval_hours * 3600)


if __name__ == "__main__":
    main()
