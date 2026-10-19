"""Helpers compartilhados para abrir bancos SQLite do aplicativo."""
from pathlib import Path
import sqlite3


def connect(path: Path) -> sqlite3.Connection:
    """Abre um banco SQLite garantindo a pasta pai e chaves estrangeiras."""
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    return connection

