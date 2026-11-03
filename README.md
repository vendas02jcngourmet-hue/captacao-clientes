# Captação de clientes

## Radar de Clientes (página web)

O `index.html` na raiz é o site do Radar de Clientes, publicado pelo GitHub Pages
(Settings → Pages → Branch `main`, pasta `/ (root)`). Os clientes entram com e-mail e senha;
a pesquisa, a IA e o banco de dados rodam no Supabase (projeto `radar-clientes`).
Para atualizar o site, basta substituir o `index.html`.

---

## Fase 1: listas pela base de CNPJ (programa em Python)

Gera, para cada cliente da plataforma, uma lista diária de empresas para ligar (100 por dia por padrão),
filtrada pelo perfil que o cliente escolheu. A base é o cadastro público de CNPJ da Receita Federal.

## Instalação

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Uso

```bash
# 1. Baixar a base do mês mais recente (cerca de 6 GB em zips)
python -m captacao baixar

# 2. Importar para o banco (só empresas ativas; --uf limita os estados e deixa o banco menor)
python -m captacao importar dados/2026-09 --uf SP

# 3. Ver quantas empresas o perfil do cliente encontra
python -m captacao contar clientes/jcn-gourmet.yaml

# 4. Gerar a lista do dia (listas/<cliente>/<data>.csv, abre direto no Excel)
python -m captacao lista clientes/jcn-gourmet.yaml

# 5. Depois das ligações, preencher a coluna "resultado" e devolver
python -m captacao resultados clientes/jcn-gourmet.yaml listas/jcn-gourmet/2026-10-19.csv
```

## Novo cliente

Copie `clientes/jcn-gourmet.yaml`, troque `cliente_id`, `nome` e os filtros. Cada cliente tem seu próprio
histórico: uma empresa nunca aparece duas vezes para o mesmo cliente, e rodar a lista de novo no mesmo dia
devolve a mesma lista.

Filtros disponíveis: CNAE (principal e secundário), estado, município, bairro, CEP, porte, MEI, data de
abertura, capital social, palavras no nome, só matriz, exigir telefone, celular ou e-mail.
Prioridade da lista: `mais_recentes`, `mais_antigas`, `maior_capital` ou `aleatorio`.

## Resultados aceitos na planilha

`nao_atendeu`, `numero_errado`, `sem_interesse`, `retornar`, `interessado`, `cliente`, `nao_ligar`.

`nao_ligar` bloqueia a empresa para todos os clientes da plataforma (pedido de exclusão, LGPD).

## Testes

```bash
pip install pytest && python -m pytest
```
