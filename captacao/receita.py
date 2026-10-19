"""Download dos dados abertos do CNPJ publicados pela Receita Federal."""

import re
from pathlib import Path

import requests

URL_BASE = "https://arquivos.receitafederal.gov.br/dados/cnpj/dados_abertos_cnpj/"

# Arquivos usados pela plataforma. Estabelecimentos e Empresas vêm divididos em 10 partes.
ARQUIVOS_TABELAS = ["Cnaes.zip", "Municipios.zip", "Simples.zip"]
ARQUIVOS_PARTES = ["Empresas", "Estabelecimentos"]


def listar_meses(url_base: str = URL_BASE) -> list[str]:
    """Retorna os meses disponíveis (AAAA-MM), do mais antigo ao mais recente."""
    resp = requests.get(url_base, timeout=60)
    resp.raise_for_status()
    return sorted(set(re.findall(r'href="(\d{4}-\d{2})/?"', resp.text)))


def arquivos_do_mes() -> list[str]:
    nomes = list(ARQUIVOS_TABELAS)
    for prefixo in ARQUIVOS_PARTES:
        nomes += [f"{prefixo}{i}.zip" for i in range(10)]
    return nomes


def baixar(destino: Path, mes: str | None = None, url_base: str = URL_BASE) -> Path:
    """Baixa os zips do mês (o mais recente se `mes` for None) para destino/AAAA-MM."""
    if mes is None:
        meses = listar_meses(url_base)
        if not meses:
            raise RuntimeError(f"Nenhum mês encontrado em {url_base}")
        mes = meses[-1]
    pasta = Path(destino) / mes
    pasta.mkdir(parents=True, exist_ok=True)
    for nome in arquivos_do_mes():
        alvo = pasta / nome
        if alvo.exists() and alvo.stat().st_size > 0:
            print(f"já existe: {alvo}")
            continue
        url = f"{url_base}{mes}/{nome}"
        print(f"baixando {url}")
        parcial = alvo.with_suffix(".parcial")
        with requests.get(url, stream=True, timeout=120) as resp:
            resp.raise_for_status()
            with open(parcial, "wb") as f:
                for bloco in resp.iter_content(chunk_size=1 << 20):
                    f.write(bloco)
        parcial.rename(alvo)
    return pasta
