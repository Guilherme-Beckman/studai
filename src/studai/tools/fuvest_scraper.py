"""
Scraper do acervo de vestibulares da FUVEST.

Fluxo:
  1. Acessa https://www.fuvest.br/acervo-vestibular e descobre a página de cada ano
  2. Abre a página do(s) ano(s) escolhido(s)
  3. Baixa:
       - Prova da 1ª fase (somente a versão escolhida, padrão V1)
       - Todas as provas do 2º dia da 2ª fase
       - Todos os gabaritos / respostas esperadas
  4. Organiza tudo em pastas e grava um manifest.json por ano

Uso (o script da raiz `fuvest_scraper.py` delega para este módulo):
  pip install requests beautifulsoup4
  PYTHONPATH=src python -m studai.tools.fuvest_scraper --dry-run

  python fuvest_scraper.py --dry-run              # só lista o que seria baixado (2026)
  python fuvest_scraper.py                        # baixa 2026
  python fuvest_scraper.py --anos 2025 2024       # outros anos
  python fuvest_scraper.py --todos                # todos os anos do acervo
  python fuvest_scraper.py --versao V2            # outra versão da prova de 1ª fase
  python fuvest_scraper.py --incluir-dia1         # inclui também a prova do 1º dia (2ª fase)
  python fuvest_scraper.py --notas-corte          # inclui as notas de corte
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

# Dependências opcionais (não estão em requirements.txt): as regras de seleção abaixo funcionam
# sem elas; só o download exige requests e beautifulsoup4.
try:
    import requests
    from bs4 import BeautifulSoup, Tag
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
except ImportError:  # pragma: no cover - depende do ambiente
    requests = BeautifulSoup = Tag = HTTPAdapter = Retry = None

BASE = "https://www.fuvest.br"
ACERVO = f"{BASE}/acervo-vestibular"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9",
}
EXTENSOES_ARQUIVO = (".pdf", ".zip", ".xlsx", ".xls", ".docx", ".doc", ".csv", ".txt")
TAGS_TITULO = ("h1", "h2", "h3", "h4", "h5", "h6", "strong", "b", "p", "div", "span")
SECAO_RE = re.compile(r"^(primeira|segunda) fase:?$")


# --------------------------------------------------------------------------- #
# Utilidades
# --------------------------------------------------------------------------- #
def norm(texto: str) -> str:
    """minúsculas, sem acento, espaços normalizados. '2º' -> '2o', '1ª' -> '1a'."""
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", texto).strip().lower()


def slug(texto: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", norm(texto)).strip("_") or "arquivo"


def criar_sessao() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    retry = Retry(total=4, backoff_factor=1.5, status_forcelist=[429, 500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


def e_arquivo_direto(url: str) -> bool:
    return urlparse(url).path.lower().endswith(EXTENSOES_ARQUIVO)


# --------------------------------------------------------------------------- #
# Passo 1: acervo -> páginas por ano
# --------------------------------------------------------------------------- #
def listar_anos(sessao: requests.Session) -> dict[int, str]:
    r = sessao.get(ACERVO, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    anos = {}
    for a in soup.find_all("a", href=True):
        m = re.search(r"acervo-vestibular-(\d{4})", a["href"])
        if m:
            anos[int(m.group(1))] = urljoin(ACERVO, a["href"])
    return anos


# --------------------------------------------------------------------------- #
# Passo 2: página do ano -> lista de links com a seção (1ª / 2ª fase)
# --------------------------------------------------------------------------- #
def extrair_itens(soup: BeautifulSoup, url_pagina: str) -> list[dict]:
    itens, vistos, secao = [], set(), None
    for el in soup.descendants:
        if not isinstance(el, Tag):
            continue

        # Títulos "Primeira Fase" / "Segunda Fase" definem a seção atual
        if el.name in TAGS_TITULO:
            m = SECAO_RE.match(norm(el.get_text(" ", strip=True)))
            if m:
                secao = m.group(1)
                continue

        if el.name == "a" and el.get("href"):
            href = el["href"].strip()
            if href.startswith(("#", "mailto:", "javascript:", "tel:")):
                continue
            texto = el.get_text(" ", strip=True)
            url = urljoin(url_pagina, href)
            if (url, texto) in vistos or not texto:
                continue
            vistos.add((url, texto))
            itens.append({"secao": secao, "texto": texto, "url": url})
    return itens


# --------------------------------------------------------------------------- #
# Passo 3: decidir o que baixar
# --------------------------------------------------------------------------- #
def classificar(item: dict, versao: str, incluir_dia1: bool, notas_corte: bool):
    """
    Retorna (pasta_fase, subpasta) ou None se o item deve ser ignorado.
    pasta_fase: '1a_fase' | '2a_fase'
    subpasta:   'provas' | 'provas_dia1' | 'provas_dia2' | 'gabaritos' | 'notas_corte'
    """
    t = norm(item["texto"])
    u = norm(unquote(item["url"]))

    # Fase: pela seção da página; se não houver, tenta inferir pelo texto/URL
    if item["secao"] == "primeira":
        fase = "1a_fase"
    elif item["secao"] == "segunda":
        fase = "2a_fase"
    elif "1a fase" in t or re.search(r"fase[-_ ]?1\b", u):
        fase = "1a_fase"
    elif "2a fase" in t or re.search(r"fase[-_ ]?2\b", u):
        fase = "2a_fase"
    else:
        return None

    # Gabaritos / respostas esperadas (todos, de todas as provas)
    if any(k in t for k in ("gabarito", "resposta esperada", "respostas esperadas",
                            "abordagem esperada")):
        return fase, "gabaritos"

    if "nota" in t and "corte" in t:
        return (fase, "notas_corte") if notas_corte else None

    if "prova" not in t:
        return None

    if fase == "1a_fase":
        m = re.search(r"\bv(\d)\b", t)
        if m and f"v{m.group(1)}" != versao.lower():
            return None  # outra versão (V2, V3, V4...)
        return fase, "provas"

    # 2ª fase: separar por dia
    m = re.search(r"\b([12])o dia", t)
    if not m:
        return None
    if m.group(1) == "2":
        return fase, "provas_dia2"
    return (fase, "provas_dia1") if incluir_dia1 else None


def expandir_link(sessao: requests.Session, url: str) -> list[str]:
    """Se o link já é um arquivo, devolve ele. Se é uma página, procura arquivos nela."""
    if e_arquivo_direto(url):
        return [url]
    try:
        r = sessao.get(url, timeout=30)
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"    ! não consegui abrir {url}: {e}")
        return []
    if "html" not in r.headers.get("Content-Type", "").lower():
        return [url]  # arquivo servido sem extensão na URL
    soup = BeautifulSoup(r.text, "html.parser")
    achados = []
    for a in soup.find_all("a", href=True):
        link = urljoin(url, a["href"])
        if e_arquivo_direto(link) and link not in achados:
            achados.append(link)
    return achados


def arquivo_do_ano(url: str, ano: int) -> bool:
    """
    Arquivo achado dentro de uma página intermediária só vale se o nome citar o ano
    (ou o anterior, data da prova): a página de 1996 leva a uma lista de provas de
    pós-graduação e do Provão 2026, que não são do vestibular.
    """
    nome = unquote(Path(urlparse(url).path).name)
    return str(ano) in nome or str(ano - 1) in nome


def nome_arquivo(url: str, texto: str) -> str:
    nome = unquote(Path(urlparse(url).path).name)
    if not nome or "." not in nome:
        nome = slug(texto) + ".pdf"
    return re.sub(r'[\\/:*?"<>|]', "_", nome)


# --------------------------------------------------------------------------- #
# Passo 4: download
# --------------------------------------------------------------------------- #
def baixar(sessao: requests.Session, url: str, destino: Path) -> str:
    if destino.exists() and destino.stat().st_size > 0:
        return "ja_existia"
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_suffix(destino.suffix + ".part")
    try:
        with sessao.get(url, stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(tmp, "wb") as f:
                for bloco in r.iter_content(64 * 1024):
                    f.write(bloco)
        if destino.suffix.lower() == ".pdf":
            with open(tmp, "rb") as f:
                if f.read(4) != b"%PDF":
                    tmp.unlink(missing_ok=True)
                    return "erro: resposta não é um PDF"
        tmp.replace(destino)
        return "baixado"
    except requests.RequestException as e:
        tmp.unlink(missing_ok=True)
        return f"erro: {e}"


# --------------------------------------------------------------------------- #
# Orquestração
# --------------------------------------------------------------------------- #
def processar_ano(sessao, ano: int, url: str, args) -> None:
    print(f"\n=== {ano} === {url}")
    try:
        r = sessao.get(url, timeout=30)
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"  ! erro ao abrir a página do ano: {e}")
        return

    soup = BeautifulSoup(r.text, "html.parser")
    itens = extrair_itens(soup, url)

    selecionados, ignorados = [], []
    for item in itens:
        destino = classificar(item, args.versao, args.incluir_dia1, args.notas_corte)
        (selecionados if destino else ignorados).append((item, destino))

    if not selecionados:
        print("  Nenhum item encontrado. A estrutura da página pode ser diferente.")
        if args.verbose:
            for item, _ in ignorados:
                print(f"    ignorado: [{item['secao']}] {item['texto']} -> {item['url']}")
        return

    pasta_ano = Path(args.saida) / str(ano)
    manifest, usados = [], {}

    for item, (fase, sub) in selecionados:
        print(f"  [{fase}/{sub}] {item['texto']}")
        for link in expandir_link(sessao, item["url"]):
            if link != item["url"] and not arquivo_do_ano(link, ano):
                print(f"      ignorado (não cita {ano}): {link}")
                continue
            nome = nome_arquivo(link, item["texto"])
            destino = pasta_ano / fase / sub / nome

            # evita sobrescrever arquivos diferentes com o mesmo nome
            if usados.get(destino, link) != link:
                destino = destino.with_name(f"{slug(item['texto'])}__{nome}")
            usados[destino] = link

            if args.dry_run:
                status = "dry-run"
                print(f"      -> {destino}\n         {link}")
            else:
                status = baixar(sessao, link, destino)
                print(f"      {status}: {destino.name}")
                time.sleep(args.delay)

            manifest.append({
                "texto": item["texto"], "fase": fase, "tipo": sub,
                "url": link, "arquivo": str(destino), "status": status,
            })

    if args.verbose:
        for item, _ in ignorados:
            print(f"  (ignorado) [{item['secao']}] {item['texto']}")

    if not args.dry_run:
        pasta_ano.mkdir(parents=True, exist_ok=True)
        (pasta_ano / "manifest.json").write_text(
            json.dumps({"ano": ano, "pagina": url, "itens": manifest},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Baixa provas e gabaritos do acervo da FUVEST")
    p.add_argument("--anos", nargs="+", type=int, default=[2026], help="anos (padrão: 2026)")
    p.add_argument("--todos", action="store_true", help="todos os anos do acervo")
    p.add_argument("--saida", default="fuvest_acervo", help="pasta de destino")
    p.add_argument("--versao", default="V1", help="versão da prova de 1ª fase (padrão: V1)")
    p.add_argument("--incluir-dia1", action="store_true", help="inclui prova do 1º dia da 2ª fase")
    p.add_argument("--notas-corte", action="store_true", help="inclui notas de corte")
    p.add_argument("--delay", type=float, default=1.0, help="pausa entre downloads (s)")
    p.add_argument("--dry-run", action="store_true", help="só lista, não baixa")
    p.add_argument("-v", "--verbose", action="store_true", help="mostra também os itens ignorados")
    args = p.parse_args(argv)
    if requests is None or BeautifulSoup is None:
        sys.exit("instale as dependências do scraper: pip install requests beautifulsoup4")

    sessao = criar_sessao()

    try:
        disponiveis = listar_anos(sessao)
    except requests.RequestException as e:
        print(f"Erro ao acessar o acervo: {e}")
        disponiveis = {}
    print(f"Anos encontrados no acervo: {sorted(disponiveis, reverse=True) or 'nenhum'}")

    anos = sorted(disponiveis, reverse=True) if args.todos else args.anos
    if not anos:
        sys.exit("Nenhum ano para processar.")

    for ano in anos:
        url = disponiveis.get(ano, f"{BASE}/acervo-vestibular-{ano}/")
        processar_ano(sessao, ano, url, args)

    print("\nConcluído.")


if __name__ == "__main__":
    main()
