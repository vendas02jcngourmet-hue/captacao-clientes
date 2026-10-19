"""Banco DuckDB: importação dos arquivos da Receita e tabelas da plataforma."""

import tempfile
import zipfile
from pathlib import Path

import duckdb

# Layout oficial dos arquivos (sem cabeçalho, separados por ';', codificação latin-1).
COLUNAS = {
    "empresas": [
        "cnpj_basico", "razao_social", "natureza_juridica", "qualificacao_responsavel",
        "capital_social", "porte_empresa", "ente_federativo",
    ],
    "estabelecimentos": [
        "cnpj_basico", "cnpj_ordem", "cnpj_dv", "matriz_filial", "nome_fantasia",
        "situacao_cadastral", "data_situacao_cadastral", "motivo_situacao_cadastral",
        "cidade_exterior", "pais", "data_inicio_atividade", "cnae_principal",
        "cnae_secundaria", "tipo_logradouro", "logradouro", "numero", "complemento",
        "bairro", "cep", "uf", "municipio", "ddd_1", "telefone_1", "ddd_2", "telefone_2",
        "ddd_fax", "fax", "email", "situacao_especial", "data_situacao_especial",
    ],
    "simples": [
        "cnpj_basico", "opcao_simples", "data_opcao_simples", "data_exclusao_simples",
        "opcao_mei", "data_opcao_mei", "data_exclusao_mei",
    ],
    "cnaes": ["codigo", "descricao"],
    "municipios": ["codigo", "descricao"],
}

# Prefixo do nome do zip -> tabela
PREFIXOS = {
    "Empresas": "empresas",
    "Estabelecimentos": "estabelecimentos",
    "Simples": "simples",
    "Cnaes": "cnaes",
    "Municipios": "municipios",
}

TABELAS_PLATAFORMA = """
CREATE TABLE IF NOT EXISTS contatos (
    cliente_id   VARCHAR NOT NULL,
    cnpj         VARCHAR NOT NULL,
    data_lista   DATE NOT NULL,
    resultado    VARCHAR DEFAULT 'pendente',
    observacao   VARCHAR,
    atualizado_em TIMESTAMP DEFAULT current_timestamp,
    PRIMARY KEY (cliente_id, cnpj)
);
CREATE TABLE IF NOT EXISTS bloqueio (
    cnpj       VARCHAR PRIMARY KEY,
    motivo     VARCHAR,
    criado_em  TIMESTAMP DEFAULT current_timestamp
);
"""


def conectar(caminho: str | Path) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(caminho))
    con.execute(TABELAS_PLATAFORMA)
    return con


def _ler_csv(con, arquivos: list[str], tabela: str) -> str:
    colunas = COLUNAS[tabela]
    tipos = ", ".join(f"'{c}': 'VARCHAR'" for c in colunas)
    lista = ", ".join(f"'{a}'" for a in arquivos)
    return (
        f"read_csv([{lista}], delim=';', quote='\"', header=false, encoding='latin-1', "
        f"columns={{{tipos}}}, ignore_errors=true, strict_mode=false)"
    )


def importar(con, pasta_zips: str | Path, ufs: list[str] | None = None, so_ativas: bool = True) -> dict:
    """Importa os zips de uma pasta para o banco, substituindo as tabelas da Receita.

    `ufs` e `so_ativas` reduzem o tamanho do banco guardando só os estabelecimentos úteis.
    """
    pasta = Path(pasta_zips)
    grupos: dict[str, list[Path]] = {}
    for zip_path in sorted(pasta.glob("*.zip")):
        for prefixo, tabela in PREFIXOS.items():
            if zip_path.name.startswith(prefixo):
                grupos.setdefault(tabela, []).append(zip_path)
    faltando = {"empresas", "estabelecimentos"} - grupos.keys()
    if faltando:
        raise FileNotFoundError(f"Faltam arquivos em {pasta}: {', '.join(sorted(faltando))}")

    contagens = {}
    with tempfile.TemporaryDirectory() as tmp:
        for tabela, zips in grupos.items():
            extraidos = []
            for i, zp in enumerate(zips):
                with zipfile.ZipFile(zp) as z:
                    for nome in z.namelist():
                        alvo = Path(tmp) / f"{tabela}_{i}_{Path(nome).name}"
                        with z.open(nome) as orig, open(alvo, "wb") as dest:
                            while bloco := orig.read(1 << 20):
                                dest.write(bloco)
                        extraidos.append(str(alvo))
            origem = _ler_csv(con, extraidos, tabela)
            filtro = []
            if tabela == "estabelecimentos":
                if so_ativas:
                    filtro.append("situacao_cadastral = '02'")
                if ufs:
                    filtro.append("uf IN (" + ", ".join(f"'{u.upper()}'" for u in ufs) + ")")
            where = (" WHERE " + " AND ".join(filtro)) if filtro else ""
            con.execute(f"CREATE OR REPLACE TABLE rf_{tabela} AS SELECT * FROM {origem}{where}")
            contagens[tabela] = con.execute(f"SELECT count(*) FROM rf_{tabela}").fetchone()[0]
            for arq in extraidos:
                Path(arq).unlink()

    for tabela in ("simples", "cnaes", "municipios"):
        if tabela not in grupos:
            cols = ", ".join(f"{c} VARCHAR" for c in COLUNAS[tabela])
            con.execute(f"CREATE OR REPLACE TABLE rf_{tabela} ({cols})")
    _criar_visao(con)
    return contagens


def _criar_visao(con) -> None:
    """Visão `leads`: uma linha por estabelecimento, já com os campos usados nos filtros."""
    con.execute("""
    CREATE OR REPLACE VIEW leads AS
    SELECT
        e.cnpj_basico || e.cnpj_ordem || e.cnpj_dv              AS cnpj,
        emp.razao_social,
        nullif(trim(e.nome_fantasia), '')                       AS nome_fantasia,
        e.matriz_filial = '1'                                   AS matriz,
        e.cnae_principal,
        cn.descricao                                            AS cnae_descricao,
        coalesce(e.cnae_secundaria, '')                         AS cnae_secundaria,
        try_strptime(nullif(e.data_inicio_atividade, '0'), '%Y%m%d')::DATE AS data_abertura,
        CASE emp.porte_empresa WHEN '01' THEN 'ME' WHEN '03' THEN 'EPP'
             WHEN '05' THEN 'DEMAIS' ELSE 'NAO_INFORMADO' END   AS porte,
        try_cast(replace(emp.capital_social, ',', '.') AS DOUBLE) AS capital_social,
        coalesce(s.opcao_mei = 'S', false)                      AS mei,
        coalesce(s.opcao_simples = 'S', false)                  AS simples,
        trim(concat_ws(' ', e.tipo_logradouro, e.logradouro, e.numero, e.complemento)) AS endereco,
        e.bairro,
        e.cep,
        e.uf,
        m.descricao                                             AS municipio,
        nullif(trim(e.ddd_1), '') || nullif(trim(e.telefone_1), '') AS telefone_1,
        nullif(trim(e.ddd_2), '') || nullif(trim(e.telefone_2), '') AS telefone_2,
        lower(nullif(trim(e.email), ''))                        AS email
    FROM rf_estabelecimentos e
    JOIN rf_empresas emp USING (cnpj_basico)
    LEFT JOIN rf_simples s USING (cnpj_basico)
    LEFT JOIN rf_cnaes cn ON cn.codigo = e.cnae_principal
    LEFT JOIN rf_municipios m ON m.codigo = e.municipio
    """)
