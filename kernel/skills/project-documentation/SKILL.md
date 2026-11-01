---
name: project-documentation
description: Produz e mantém documentação técnica de projeto no second brain do tenant, em tenants/<tenant>/projects-doc/ (arquitetura, APIs, configuração, operação, runbooks, decisões). Separa projeto de domínio de negócio, usa resources/ para material bruto, normaliza frontmatter e atualiza índices. Use quando o pedido for documentar como um projeto ou serviço funciona, reorganizar projects-doc ou extrair a parte técnica de um material misto.
metadata:
  version: "1.0.0"
  hermes:
    tags: [knowledge, second-brain, documentation, projects]
---

# Documentação de projeto

## Missão

Produzir e manter documentação de **projeto**: clara, linkável e boa para busca semântica, em
`$VAULT_PATH/tenants/$JARVIS_TENANT/projects-doc/<projeto>/...`.

Não escreva aqui conteúdo primário sobre o domínio de negócio (conceitos, regras, glossário): isso mora em
`knowledge-domain/`. Material capturado ou misto ainda não canônico fica em `resources/` até ser organizado.

## Pergunta-guia

*Como este projeto funciona dentro do tenant?*

## Quando usar

- Pergunta sobre como um projeto funciona: componentes, fluxos, configuração, observabilidade,
  troubleshooting, decisões de arquitetura.
- Normalizar ou reorganizar material técnico em `projects-doc/`.
- Extrair a parte de implementação de um material misto.

## Quando não usar

- O foco é conceito de negócio ou de domínio, sem amarração a um código.
- O pedido é só explorar código, sem produzir nem manter documentação.
- A pessoa proibiu mudanças em arquivos de documentação.

## Classificação (antes de editar)

Para cada item: `project-documentation` | `knowledge-base` | `resource-base` | `mixed` | `obsolete` |
`duplicate`.

- **project-documentation:** decisões, componentes, fluxos, deploy, filas, retries **deste** código.
- **knowledge-base:** continuaria útil mesmo se o repositório sumisse; não é alvo de escrita aqui, só
  link em "Notas de domínio relacionadas".
- **resource-base:** captura, nota crua, rascunho misto; deve morar em `resources/` até normalizar.
- **mixed:** divida: a parte técnica vai para `projects-doc/`, com link para a nota de domínio.
- **obsolete / duplicate:** proponha arquivar ou consolidar, apontando a página canônica.

Relate as decisões e dúvidas **antes** de edições grandes. Se incerto, classifique provisoriamente,
explique e peça confirmação.

## Fluxo

1. **Inventário:** mapeie os `.md` e as fontes relevantes (README, OpenAPI, código).
2. **Classificação:** rotule cada item; relate antes de editar.
3. **Plano:** para mudanças destrutivas, liste moves, divisões, depreciações e a estrutura alvo.
4. **Execução:** crie, mova ou reescreva conforme o plano.
5. **Normalização:** frontmatter (`domain: project-documentation`), títulos, wikilinks moderados, nomes
   semânticos, `title:` específico (ver `knowledge-write`).
6. **Índices:** atualize `projects-doc/README.md`, índices locais e os links cruzados para `resources/` e
   `knowledge-domain/`.

## O que entregar

- Páginas em `projects-doc/<projeto>/` (por exemplo `architecture/`, `api/`, `setup/`, `operations/`,
  `runbooks/`, `decisions/`).
- Frontmatter completo (abaixo).
- Seções preferidas: Propósito, Escopo, Arquitetura, Componentes, Fluxo técnico, Configuração, Operação,
  Riscos e limites, Documentos relacionados, Notas de domínio relacionadas.

## Frontmatter

```yaml
---
title: "Título humano e específico"
domain: project-documentation
type: architecture   # setup | api | runbook | adr | operations | ...
project: nome-do-projeto
status: active
owner: ""
related_systems: []
tags: []
canonical: true
tenant: <tenant>
document_role: primary
last_reviewed_at: "YYYY-MM-DD"
---
```

## Lembretes de Obsidian

- Um tópico principal por arquivo.
- `[[wikilinks]]` onde fortalecem o grafo; sem excesso.
- Nome que pode não ser único: prefira o caminho (`[[projeto/arquitetura/visao-geral]]`).

## Anti-confusão

1. Nem todo Markdown de um repositório é documentação de projeto.
2. Conteúdo primário de negócio não entra em `projects-doc/`.
3. Não misture implementação e semântica de domínio numa página final quando deveriam estar separadas.
4. Runbooks e deploy não entram em `knowledge-domain/`.
5. `resources/` não é destino final.
6. Preserve o significado ao renomear.
7. Otimize para navegação humana e para chunking de LLM.

## Encerramento (obrigatório)

```text
## Resumo da sessão de documentação
| Arquivo | Classificação | Ação | Links cruzados |
|---|---|---|---|
| caminho | project-documentation / knowledge-base / resource-base / mixed / obsolete / duplicate | created / updated / moved / split / deprecated | ... |
Incerteza: (nenhuma | descrever)
Índices atualizados: (arquivos tocados)
```
