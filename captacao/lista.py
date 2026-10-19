"""Lista diária de contatos por cliente e registro dos resultados das ligações."""

import csv
import datetime as dt
import re
from pathlib import Path

from .perfis import Perfil, condicoes, ordem, sql_celular

COLUNAS_SAIDA = [
    "cnpj", "razao_social", "nome_fantasia", "telefone", "telefone_2", "celular",
    "whatsapp", "email", "endereco", "bairro", "municipio", "uf", "cep",
    "cnae_principal", "cnae_descricao", "porte", "mei", "data_abertura",
    "resultado", "observacao",
]

# Resultados aceitos ao devolver a planilha preenchida
RESULTADOS = {
    "pendente", "nao_atendeu", "numero_errado", "sem_interesse", "retornar",
    "interessado", "cliente", "nao_ligar",
}


def formatar_telefone(numero: str | None) -> str:
    if not numero:
        return ""
    d = re.sub(r"\D", "", numero)
    if len(d) == 11:
        return f"({d[:2]}) {d[2:7]}-{d[7:]}"
    if len(d) == 10:
        return f"({d[:2]}) {d[2:6]}-{d[6:]}"
    return d


def link_whatsapp(numero: str | None) -> str:
    """Link wa.me para o vendedor abrir a conversa manualmente (celular de 8 dígitos ganha o 9)."""
    if not numero:
        return ""
    d = re.sub(r"\D", "", numero)
    if len(d) == 10 and d[2] in "6789":
        d = d[:2] + "9" + d[2:]
    return f"https://wa.me/55{d}" if len(d) == 11 else ""


def _excluidos_do_arquivo(perfil: Perfil) -> set[str]:
    """CNPJs da planilha de exclusão do cliente (ex.: quem já é cliente dele)."""
    if not perfil.arquivo_exclusao or not perfil.arquivo_exclusao.exists():
        return set()
    with open(perfil.arquivo_exclusao, encoding="utf-8-sig", newline="") as f:
        return {re.sub(r"\D", "", linha[0]) for linha in csv.reader(f) if linha and re.sub(r"\D", "", linha[0])}


def gerar_lista(con, perfil: Perfil, data: dt.date | None = None) -> list[dict]:
    """Seleciona até `limite_diario` empresas novas para o cliente e reserva para a data.

    Rodar de novo no mesmo dia devolve a mesma lista, sem reservar mais empresas.
    """
    data = data or dt.date.today()
    ja_reservados = con.execute(
        "SELECT count(*) FROM contatos WHERE cliente_id = ? AND data_lista = ?",
        [perfil.cliente_id, data],
    ).fetchone()[0]
    faltam = perfil.limite_diario - ja_reservados

    if faltam > 0:
        where, params = condicoes(perfil)
        order = ordem(perfil)
        params_ordem = [f"{perfil.cliente_id}-{data}"] if "?" in order else []
        excluidos = sorted(_excluidos_do_arquivo(perfil))
        con.execute("CREATE OR REPLACE TEMP TABLE excluidos_cliente (cnpj VARCHAR)")
        if excluidos:
            con.executemany("INSERT INTO excluidos_cliente VALUES (?)", [[c] for c in excluidos])
        con.execute(
            f"""
            INSERT INTO contatos (cliente_id, cnpj, data_lista)
            SELECT ?, cnpj, ? FROM leads
            WHERE {where}
              AND cnpj NOT IN (SELECT cnpj FROM contatos WHERE cliente_id = ?)
              AND cnpj NOT IN (SELECT cnpj FROM bloqueio)
              AND cnpj NOT IN (SELECT cnpj FROM excluidos_cliente)
            ORDER BY {order}
            LIMIT ?
            """,
            [perfil.cliente_id, data, *params, perfil.cliente_id, *params_ordem, faltam],
        )

    cur = con.execute(
        f"""
        SELECT l.*, c.resultado, c.observacao,
               {sql_celular('l.telefone_1')} AS cel_1,
               {sql_celular('l.telefone_2')} AS cel_2
        FROM contatos c JOIN leads l USING (cnpj)
        WHERE c.cliente_id = ? AND c.data_lista = ?
        ORDER BY l.municipio, l.bairro, l.cnpj
        """,
        [perfil.cliente_id, data],
    )
    nomes = [d[0] for d in cur.description]
    linhas = []
    for valores in cur.fetchall():
        r = dict(zip(nomes, valores))
        celular = r["telefone_1"] if r["cel_1"] else (r["telefone_2"] if r["cel_2"] else None)
        linhas.append({
            **{k: r.get(k) for k in COLUNAS_SAIDA if k in r},
            "telefone": formatar_telefone(r["telefone_1"] or r["telefone_2"]),
            "telefone_2": formatar_telefone(r["telefone_2"]) if r["telefone_1"] else "",
            "celular": "sim" if celular else "nao",
            "whatsapp": link_whatsapp(celular),
            "mei": "sim" if r["mei"] else "nao",
            "data_abertura": r["data_abertura"].isoformat() if r["data_abertura"] else "",
            "observacao": r["observacao"] or "",
        })
    return linhas


def salvar_csv(linhas: list[dict], caminho: str | Path) -> Path:
    caminho = Path(caminho)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig e ';' para abrir certo no Excel em português
    with open(caminho, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUNAS_SAIDA, delimiter=";", extrasaction="ignore")
        w.writeheader()
        w.writerows(linhas)
    return caminho


def registrar_resultados(con, cliente_id: str, caminho: str | Path) -> dict:
    """Lê a planilha devolvida pelo vendedor e grava o resultado de cada ligação.

    `nao_ligar` também entra na lista de bloqueio geral, valendo para todos os clientes.
    """
    contagem = {"atualizados": 0, "ignorados": 0}
    with open(caminho, encoding="utf-8-sig", newline="") as f:
        amostra = f.read(4096)
        f.seek(0)
        delim = ";" if amostra.count(";") >= amostra.count(",") else ","
        for linha in csv.DictReader(f, delimiter=delim):
            cnpj = re.sub(r"\D", "", linha.get("cnpj") or "")
            resultado = (linha.get("resultado") or "").strip().lower()
            if not cnpj or not resultado or resultado == "pendente":
                continue
            if resultado not in RESULTADOS:
                contagem["ignorados"] += 1
                print(f"resultado desconhecido para {cnpj}: {resultado!r}")
                continue
            n = con.execute(
                """UPDATE contatos SET resultado = ?, observacao = ?, atualizado_em = current_timestamp
                   WHERE cliente_id = ? AND cnpj = ? RETURNING 1""",
                [resultado, (linha.get("observacao") or "").strip() or None, cliente_id, cnpj],
            ).fetchall()
            if not n:
                contagem["ignorados"] += 1
                continue
            contagem["atualizados"] += 1
            if resultado == "nao_ligar":
                con.execute(
                    "INSERT INTO bloqueio (cnpj, motivo) VALUES (?, ?) ON CONFLICT DO NOTHING",
                    [cnpj, f"pedido via {cliente_id}"],
                )
    return contagem


def resumo(con, cliente_id: str) -> list[tuple]:
    return con.execute(
        "SELECT resultado, count(*) FROM contatos WHERE cliente_id = ? GROUP BY 1 ORDER BY 2 DESC",
        [cliente_id],
    ).fetchall()
