"""Perfis de cliente: cada cliente da plataforma descreve em um arquivo YAML quem quer captar."""

import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

PORTES = {"ME", "EPP", "DEMAIS", "NAO_INFORMADO"}
PRIORIDADES = {"mais_recentes", "mais_antigas", "maior_capital", "aleatorio"}


@dataclass
class Perfil:
    cliente_id: str
    nome: str
    limite_diario: int = 100
    cnaes: list[str] = field(default_factory=list)
    incluir_cnae_secundario: bool = False
    ufs: list[str] = field(default_factory=list)
    municipios: list[str] = field(default_factory=list)
    bairros: list[str] = field(default_factory=list)
    ceps_prefixo: list[str] = field(default_factory=list)
    portes: list[str] = field(default_factory=list)
    mei: bool | None = None
    abertura_desde: dt.date | None = None
    abertura_ate: dt.date | None = None
    capital_minimo: float | None = None
    capital_maximo: float | None = None
    palavras_chave: list[str] = field(default_factory=list)
    palavras_excluir: list[str] = field(default_factory=list)
    so_matriz: bool = False
    exigir_telefone: bool = True
    exigir_celular: bool = False
    exigir_email: bool = False
    prioridade: str = "mais_recentes"
    arquivo_exclusao: Path | None = None


def _lista(valor) -> list[str]:
    if valor is None:
        return []
    if isinstance(valor, (str, int)):
        valor = [valor]
    return [str(v).strip() for v in valor if str(v).strip()]


def _data(valor) -> dt.date | None:
    if valor is None or isinstance(valor, dt.date):
        return valor
    return dt.date.fromisoformat(str(valor))


def carregar(caminho: str | Path) -> Perfil:
    caminho = Path(caminho)
    dados = yaml.safe_load(caminho.read_text(encoding="utf-8")) or {}
    f = dados.get("filtros") or {}
    cliente_id = str(dados.get("cliente_id") or caminho.stem)
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", cliente_id):
        raise ValueError(f"cliente_id inválido: {cliente_id!r} (use letras minúsculas, números, - e _)")

    perfil = Perfil(
        cliente_id=cliente_id,
        nome=str(dados.get("nome") or cliente_id),
        limite_diario=int(dados.get("limite_diario", 100)),
        cnaes=[re.sub(r"\D", "", c) for c in _lista(f.get("cnaes"))],
        incluir_cnae_secundario=bool(f.get("incluir_cnae_secundario", False)),
        ufs=[u.upper() for u in _lista(f.get("ufs"))],
        municipios=[m.upper() for m in _lista(f.get("municipios"))],
        bairros=[b.upper() for b in _lista(f.get("bairros"))],
        ceps_prefixo=[re.sub(r"\D", "", c) for c in _lista(f.get("ceps_prefixo"))],
        portes=[p.upper() for p in _lista(f.get("portes"))],
        mei=f.get("mei"),
        abertura_desde=_data(f.get("abertura_desde")),
        abertura_ate=_data(f.get("abertura_ate")),
        capital_minimo=f.get("capital_minimo"),
        capital_maximo=f.get("capital_maximo"),
        palavras_chave=[p.upper() for p in _lista(f.get("palavras_chave"))],
        palavras_excluir=[p.upper() for p in _lista(f.get("palavras_excluir"))],
        so_matriz=bool(f.get("so_matriz", False)),
        exigir_telefone=bool(f.get("exigir_telefone", True)),
        exigir_celular=bool(f.get("exigir_celular", False)),
        exigir_email=bool(f.get("exigir_email", False)),
        prioridade=str(dados.get("prioridade", "mais_recentes")),
    )
    if dados.get("arquivo_exclusao"):
        perfil.arquivo_exclusao = (caminho.parent / dados["arquivo_exclusao"]).resolve()

    if perfil.limite_diario < 1:
        raise ValueError("limite_diario deve ser maior que zero")
    if invalidos := set(perfil.portes) - PORTES:
        raise ValueError(f"porte inválido: {sorted(invalidos)}; use {sorted(PORTES)}")
    if perfil.prioridade not in PRIORIDADES:
        raise ValueError(f"prioridade inválida: {perfil.prioridade}; use {sorted(PRIORIDADES)}")
    return perfil


# Celular: 9 dígitos começando com 9, ou o formato antigo de 8 dígitos começando com 6 a 9.
SQL_CELULAR = (
    "(regexp_full_match(substr({t}, 3), '9[0-9]{{8}}') "
    "OR regexp_full_match(substr({t}, 3), '[6-9][0-9]{{7}}'))"
)


def sql_celular(coluna: str) -> str:
    return SQL_CELULAR.format(t=coluna)


def condicoes(perfil: Perfil) -> tuple[str, list]:
    """Monta o WHERE (sobre a visão `leads`) e os parâmetros do perfil."""
    cond, params = [], []

    def em(coluna: str, valores: list):
        cond.append(f"{coluna} IN ({', '.join('?' * len(valores))})")
        params.extend(valores)

    if perfil.cnaes:
        partes = []
        for c in perfil.cnaes:
            partes.append("cnae_principal LIKE ?")
            params.append(c + "%")
            if perfil.incluir_cnae_secundario:
                # cnae_secundaria é uma lista separada por vírgula
                partes.append("list_has_any(list_transform(string_split(cnae_secundaria, ','), x -> x[1:" + str(len(c)) + "]), [?])")
                params.append(c)
        cond.append("(" + " OR ".join(partes) + ")")
    if perfil.ufs:
        em("uf", perfil.ufs)
    if perfil.municipios:
        em("upper(municipio)", perfil.municipios)
    if perfil.bairros:
        em("upper(trim(bairro))", perfil.bairros)
    if perfil.ceps_prefixo:
        cond.append("(" + " OR ".join("cep LIKE ?" for _ in perfil.ceps_prefixo) + ")")
        params.extend(c + "%" for c in perfil.ceps_prefixo)
    if perfil.portes:
        em("porte", perfil.portes)
    if perfil.mei is not None:
        cond.append("mei = ?")
        params.append(bool(perfil.mei))
    if perfil.abertura_desde:
        cond.append("data_abertura >= ?")
        params.append(perfil.abertura_desde)
    if perfil.abertura_ate:
        cond.append("data_abertura <= ?")
        params.append(perfil.abertura_ate)
    if perfil.capital_minimo is not None:
        cond.append("capital_social >= ?")
        params.append(float(perfil.capital_minimo))
    if perfil.capital_maximo is not None:
        cond.append("capital_social <= ?")
        params.append(float(perfil.capital_maximo))
    nome = "upper(coalesce(nome_fantasia, '') || ' ' || coalesce(razao_social, ''))"
    if perfil.palavras_chave:
        cond.append("(" + " OR ".join(f"{nome} LIKE ?" for _ in perfil.palavras_chave) + ")")
        params.extend(f"%{p}%" for p in perfil.palavras_chave)
    for p in perfil.palavras_excluir:
        cond.append(f"{nome} NOT LIKE ?")
        params.append(f"%{p}%")
    if perfil.so_matriz:
        cond.append("matriz")
    if perfil.exigir_celular:
        cond.append(f"({sql_celular('telefone_1')} OR {sql_celular('telefone_2')})")
    elif perfil.exigir_telefone:
        cond.append("(telefone_1 IS NOT NULL OR telefone_2 IS NOT NULL)")
    if perfil.exigir_email:
        cond.append("email IS NOT NULL")

    return (" AND ".join(cond) or "true"), params


def ordem(perfil: Perfil) -> str:
    return {
        "mais_recentes": "data_abertura DESC NULLS LAST, cnpj",
        "mais_antigas": "data_abertura ASC NULLS LAST, cnpj",
        "maior_capital": "capital_social DESC NULLS LAST, cnpj",
        "aleatorio": "hash(cnpj || ?)",
    }[perfil.prioridade]
