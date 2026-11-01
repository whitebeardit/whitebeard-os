---
name: knowledge
description: Consulta somente leitura ao second brain do tenant (o cérebro) antes de responder sobre a empresa, projetos, processos, clientes ou domínio. Use o MCP `knowledge` (GBrain local) e, como alternativa, o vault em $VAULT_PATH. Nunca escreve.
metadata:
  version: "1.0.0"
  hermes:
    tags: [knowledge, second-brain, obsidian, read-only]
---

# Consultar o second brain

## Objetivo

Responder com base no conhecimento canônico do tenant, não de memória. Antes de afirmar algo sobre a
empresa, um projeto, um processo, um cliente ou o domínio de negócio, consulte o cérebro.

## Fontes, em ordem

1. **MCP `knowledge`** (GBrain local, busca semântica e grafo). É a forma preferida: devolve as páginas com
   caminho, tags e revisão.
2. **O vault em `$VAULT_PATH`** (pasta do second brain, aberta no Obsidian). Comece pelo `README.md` da
   raiz e pelo índice `tenants/$JARVIS_TENANT/README.md`; navegue e procure a partir deles.
3. O índice do GBrain é uma projeção pesquisável. **Os arquivos do vault são a fonte da verdade.**

## Ordem de autoridade

1. normative (regra, decisão, contrato)
2. descriptive (como algo funciona hoje)
3. inferred (dedução a partir de outras notas)
4. raw (captura, transcrição, rascunho em `resources/`)

Uma nota inferida, bruta ou proposta não substitui uma nota normativa.

## Procedimento

1. Pesquise (MCP ou vault).
2. Prefira páginas com `canonical: true` e `status: active`.
3. Confira tenant, status, autoridade, vigência e `last_reviewed_at`.
4. Cite os caminhos das páginas usadas.
5. Sinalize conflitos entre páginas e ausência de fonte.
6. Não invente quando a busca não devolver evidência: diga que não há registro.

## Escrita

Esta skill é somente leitura. Para criar ou alterar páginas, use `knowledge-write`. Não:

- modifique, crie ou apague notas do vault;
- faça commit ou push no vault (o kernel sincroniza sozinho);
- reindexe ou sincronize o cérebro à mão.

## Segurança

Não revele tokens, segredos, credenciais, dados pessoais ou financeiros restritos, nem conteúdo fora da
autorização de quem pergunta.
