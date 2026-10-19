"""Linha de comando da plataforma de captação.

Exemplos:
    python -m captacao baixar
    python -m captacao importar dados/2026-09 --uf SP --uf MG
    python -m captacao contar clientes/jcn-gourmet.yaml
    python -m captacao lista clientes/jcn-gourmet.yaml
    python -m captacao resultados clientes/jcn-gourmet.yaml listas/jcn-gourmet/2026-10-19.csv
"""

import argparse
import datetime as dt
from pathlib import Path

from . import banco, lista, perfis, receita

BANCO_PADRAO = "captacao.duckdb"


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="captacao", description="Captação de clientes a partir da base de CNPJ")
    p.add_argument("--banco", default=BANCO_PADRAO, help="arquivo do banco (padrão: captacao.duckdb)")
    sub = p.add_subparsers(dest="comando", required=True)

    s = sub.add_parser("baixar", help="baixa os dados abertos do CNPJ da Receita")
    s.add_argument("--destino", default="dados")
    s.add_argument("--mes", help="AAAA-MM (padrão: o mais recente)")

    s = sub.add_parser("importar", help="importa os zips baixados para o banco")
    s.add_argument("pasta")
    s.add_argument("--uf", action="append", help="importar só estes estados (pode repetir)")
    s.add_argument("--incluir-inativas", action="store_true")

    s = sub.add_parser("contar", help="mostra quantas empresas o perfil encontra")
    s.add_argument("perfil")

    s = sub.add_parser("lista", help="gera a lista de contatos do dia para um cliente")
    s.add_argument("perfil")
    s.add_argument("--data", help="AAAA-MM-DD (padrão: hoje)")
    s.add_argument("--saida", default="listas")

    s = sub.add_parser("resultados", help="grava os resultados da planilha devolvida")
    s.add_argument("perfil")
    s.add_argument("planilha")

    a = p.parse_args(argv)

    if a.comando == "baixar":
        pasta = receita.baixar(Path(a.destino), a.mes)
        print(f"arquivos em {pasta}")
        return

    con = banco.conectar(a.banco)

    if a.comando == "importar":
        contagens = banco.importar(con, a.pasta, ufs=a.uf, so_ativas=not a.incluir_inativas)
        for tabela, n in contagens.items():
            print(f"{tabela}: {n:,} linhas".replace(",", "."))
        return

    perfil = perfis.carregar(a.perfil)

    if a.comando == "contar":
        where, params = perfis.condicoes(perfil)
        total = con.execute(f"SELECT count(*) FROM leads WHERE {where}", params).fetchone()[0]
        ja = con.execute("SELECT count(*) FROM contatos WHERE cliente_id = ?", [perfil.cliente_id]).fetchone()[0]
        print(f"{perfil.nome}: {total:,} empresas no perfil, {ja:,} já reservadas".replace(",", "."))
        if perfil.limite_diario:
            dias = max(total - ja, 0) // perfil.limite_diario
            print(f"dá para cerca de {dias} dias de lista com {perfil.limite_diario} por dia")

    elif a.comando == "lista":
        data = dt.date.fromisoformat(a.data) if a.data else dt.date.today()
        linhas = lista.gerar_lista(con, perfil, data)
        caminho = lista.salvar_csv(linhas, Path(a.saida) / perfil.cliente_id / f"{data}.csv")
        print(f"{len(linhas)} contatos para {perfil.nome} em {caminho}")
        if len(linhas) < perfil.limite_diario:
            print("aviso: o perfil não tem empresas novas suficientes; amplie os filtros")

    elif a.comando == "resultados":
        c = lista.registrar_resultados(con, perfil.cliente_id, a.planilha)
        print(f"{c['atualizados']} resultados gravados, {c['ignorados']} ignorados")
        for resultado, n in lista.resumo(con, perfil.cliente_id):
            print(f"  {resultado}: {n}")


if __name__ == "__main__":
    main()
