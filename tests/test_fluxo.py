"""Testa o fluxo completo com arquivos pequenos no mesmo formato dos da Receita."""

import csv
import datetime as dt
import zipfile

import pytest

from captacao import banco, lista, perfis


def _zip(pasta, nome_zip, nome_csv, linhas):
    conteudo = "\n".join(";".join(f'"{v}"' for v in linha) for linha in linhas) + "\n"
    with zipfile.ZipFile(pasta / nome_zip, "w") as z:
        z.writestr(nome_csv, conteudo.encode("latin-1"))


def _estab(basico, fantasia, cnae, uf, mun, abertura, ddd, tel, situacao="02", bairro="CENTRO", secundaria=""):
    return [basico, "0001", "00", "1", fantasia, situacao, "20200101", "00", "", "", abertura, cnae,
            secundaria, "RUA", "DAS FLORES", "10", "", bairro, "13000000", uf, mun, ddd, tel, "", "",
            "", "", f"contato@{basico}.com.br", "", ""]


@pytest.fixture
def con(tmp_path):
    dados = tmp_path / "zips"
    dados.mkdir()
    _zip(dados, "Empresas0.zip", "K.EMPRECSV", [
        ["11111111", "RESTAURANTE SABOR LTDA", "2062", "49", "50000,00", "01", ""],
        ["22222222", "LANCHES DO ZÉ ME", "2135", "50", "5000,00", "01", ""],
        ["33333333", "PADARIA PÃO BOM LTDA", "2062", "49", "200000,00", "03", ""],
        ["44444444", "OFICINA MECANICA LTDA", "2062", "49", "10000,00", "01", ""],
        ["55555555", "RESTAURANTE FECHADO LTDA", "2062", "49", "10000,00", "01", ""],
        ["66666666", "RESTAURANTE RIO LTDA", "2062", "49", "10000,00", "01", ""],
        ["77777777", "BUFFET SEM TELEFONE LTDA", "2062", "49", "10000,00", "01", ""],
    ])
    _zip(dados, "Estabelecimentos0.zip", "K.ESTABELE", [
        _estab("11111111", "SABOR", "5611201", "SP", "6291", "20240310", "19", "991234567"),
        _estab("22222222", "ZE LANCHES", "5611203", "SP", "6291", "20230105", "19", "32345678", bairro="CAMBUI"),
        _estab("33333333", "PAO BOM", "4721102", "SP", "7107", "20180720", "11", "87654321"),
        _estab("44444444", "OFICINA", "4520001", "SP", "6291", "20220101", "19", "33334444"),
        _estab("55555555", "FECHADO", "5611201", "SP", "6291", "20210101", "19", "33335555", situacao="08"),
        _estab("66666666", "RIO", "5611201", "RJ", "6001", "20240101", "21", "998887777"),
        _estab("77777777", "BUFFET", "8230002", "SP", "6291", "20240601", "", "", secundaria="5620102,5611201"),
    ])
    _zip(dados, "Simples.zip", "K.SIMPLES", [
        ["22222222", "S", "20230105", "00000000", "S", "20230105", "00000000"],
    ])
    _zip(dados, "Cnaes.zip", "K.CNAECSV", [["5611201", "Restaurantes e similares"], ["5611203", "Lanchonetes"]])
    _zip(dados, "Municipios.zip", "K.MUNICCSV", [["6291", "CAMPINAS"], ["7107", "SAO PAULO"], ["6001", "RIO DE JANEIRO"]])

    c = banco.conectar(tmp_path / "teste.duckdb")
    contagens = banco.importar(c, dados)
    assert contagens["estabelecimentos"] == 6  # a empresa baixada (situação 08) fica de fora
    return c


def _perfil(tmp_path, texto, nome="cliente-a"):
    arq = tmp_path / f"{nome}.yaml"
    arq.write_text(texto, encoding="utf-8")
    return perfis.carregar(arq)


def test_importacao_monta_campos(con):
    linha = con.execute("SELECT razao_social, porte, mei, capital_social, municipio, data_abertura FROM leads WHERE cnpj = '22222222000100'").fetchone()
    assert linha == ("LANCHES DO ZÉ ME", "ME", True, 5000.0, "CAMPINAS", dt.date(2023, 1, 5))


def test_filtros_cnae_uf_e_mei(con, tmp_path):
    p = _perfil(tmp_path, "filtros:\n  cnaes: ['5611', '4721']\n  ufs: [SP]\n  mei: false\n")
    where, params = perfis.condicoes(p)
    cnpjs = {r[0][:8] for r in con.execute(f"SELECT cnpj FROM leads WHERE {where}", params).fetchall()}
    assert cnpjs == {"11111111", "33333333"}


def test_cnae_secundario_e_telefone(con, tmp_path):
    p = _perfil(tmp_path, "filtros:\n  cnaes: ['5620']\n  incluir_cnae_secundario: true\n  exigir_telefone: false\n")
    where, params = perfis.condicoes(p)
    assert [r[0][:8] for r in con.execute(f"SELECT cnpj FROM leads WHERE {where}", params).fetchall()] == ["77777777"]
    p.exigir_telefone = True
    where, params = perfis.condicoes(p)
    assert con.execute(f"SELECT count(*) FROM leads WHERE {where}", params).fetchone()[0] == 0


def test_celular(con, tmp_path):
    p = _perfil(tmp_path, "filtros:\n  exigir_celular: true\n")
    where, params = perfis.condicoes(p)
    cnpjs = {r[0][:8] for r in con.execute(f"SELECT cnpj FROM leads WHERE {where}", params).fetchall()}
    assert cnpjs == {"11111111", "33333333", "66666666"}  # 9 dígitos com 9, ou 8 dígitos começando com 6-9


def test_lista_diaria_nao_repete_e_respeita_limite(con, tmp_path):
    (tmp_path / "excluir.csv").write_text("cnpj\n33.333.333/0001-00\n", encoding="utf-8")
    p = _perfil(tmp_path, "limite_diario: 1\narquivo_exclusao: excluir.csv\nfiltros:\n  cnaes: ['5611', '4721']\n  ufs: [SP]\n")
    dia1, dia2, dia3 = dt.date(2026, 10, 19), dt.date(2026, 10, 20), dt.date(2026, 10, 21)

    l1 = lista.gerar_lista(con, p, dia1)
    assert [x["cnpj"] for x in l1] == ["11111111000100"]  # mais recente primeiro
    assert l1[0]["whatsapp"] == "https://wa.me/5519991234567"
    assert lista.gerar_lista(con, p, dia1) == l1  # mesmo dia, mesma lista

    l2 = lista.gerar_lista(con, p, dia2)
    assert [x["cnpj"] for x in l2] == ["22222222000100"]
    assert lista.gerar_lista(con, p, dia3) == []  # 33333333 está na exclusão do cliente


def test_resultados_e_bloqueio_geral(con, tmp_path):
    a = _perfil(tmp_path, "limite_diario: 5\nfiltros:\n  cnaes: ['5611']\n  ufs: [SP]\n", "cliente-a")
    b = _perfil(tmp_path, "limite_diario: 5\nfiltros:\n  cnaes: ['5611']\n  ufs: [SP]\n", "cliente-b")
    dia = dt.date(2026, 10, 19)
    linhas = lista.gerar_lista(con, a, dia)
    planilha = lista.salvar_csv(linhas, tmp_path / "saida.csv")

    with open(planilha, encoding="utf-8-sig", newline="") as f:
        dados = list(csv.DictReader(f, delimiter=";"))
    for d in dados:
        d["resultado"] = "nao_ligar" if d["cnpj"].startswith("1111") else "interessado"
    with open(planilha, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=lista.COLUNAS_SAIDA, delimiter=";")
        w.writeheader()
        w.writerows(dados)

    assert lista.registrar_resultados(con, "cliente-a", planilha) == {"atualizados": 2, "ignorados": 0}
    assert dict(lista.resumo(con, "cliente-a")) == {"nao_ligar": 1, "interessado": 1}
    # quem pediu para não ligar não aparece para nenhum outro cliente
    assert [x["cnpj"][:8] for x in lista.gerar_lista(con, b, dia)] == ["22222222"]


def test_perfil_invalido(tmp_path):
    with pytest.raises(ValueError):
        _perfil(tmp_path, "filtros:\n  portes: [GRANDE]\n")
