---
name: knowledge-write
description: Roteia a criação e a edição de páginas no second brain do tenant (vault em $VAULT_PATH): classifica o conteúdo (projeto, domínio, recurso bruto), delega à skill certa e garante página navegável (título, links, índices). Use sempre que for criar uma nota nova ou editar substancialmente uma existente.
metadata:
  version: "1.0.0"
  hermes:
    tags: [knowledge, second-brain, obsidian, write]
---

# Escrever no second brain

## Propósito

Um único fluxo para documentar e organizar conhecimento no vault do tenant, delegando o detalhe a skills
especializadas. É a metade de escrita de `knowledge` (que é só leitura).

## Onde

- **Vault:** `$VAULT_PATH` (pasta do second brain; abre no Obsidian). Caminho fixo, não depende de onde a
  sessão foi aberta.
- **Tenant:** `$JARVIS_TENANT`. Tudo o que é do tenant mora em `tenants/$JARVIS_TENANT/`:
  - `projects-doc/`: como os projetos e sistemas funcionam (arquitetura, configuração, operação, decisões).
  - `knowledge-domain/`: conhecimento do negócio e da área, que vale mesmo sem nenhum sistema.
  - `resources/`: pouso temporário de material bruto, capturas e rascunhos ainda não canônicos.

## Como gravar

- **Com acesso a arquivos** (Claude Code, terminal): edite os arquivos em `$VAULT_PATH` diretamente.
- **Sem acesso a arquivos** (assistentes do Telegram, Discord, WhatsApp): use o MCP `vault`:
  - `read_note(path)` devolve o conteúdo e a `version` (ou `exists: false`);
  - `write_note(path, content)` cria uma nota; para substituir uma que já existe, mande a `version` lida;
  - `edit_note(path, old_text, new_text)` troca um trecho exato (acrescentar uma linha numa tabela, atualizar um
    parágrafo).

  O caminho é relativo ao vault e a gravação só vale dentro de `tenants/$JARVIS_TENANT/`. Se a resposta for
  "a nota mudou desde que você leu", leia de novo e refaça a alteração.
- **Não use** `put_page`, `edit_page` nem `add_timeline_entry` do MCP `knowledge`: a fonte do vault é só de
  leitura para o cérebro (os arquivos são a fonte da verdade) e essas escritas são recusadas. O `knowledge`
  continua sendo a busca; o que você gravar entra nela no próximo ciclo de sincronização (poucos minutos).

## Delegação

| Situação | Skill |
|---|---|
| Documentação técnica de projeto, arquitetura, APIs, runbooks, reorganizar `projects-doc/` | `project-documentation` |
| Conceito, regra ou glossário do domínio de negócio (sem amarração a um código) | escreva em `knowledge-domain/` seguindo as regras abaixo; se o tenant tiver uma skill de domínio própria, use-a |
| Conteúdo misto | classifique cada parte, divida em páginas e ligue uma à outra |

Ordem lógica em qualquer caso: **inventário → classificação → plano → execução → normalização →
atualização de índices**.

## Regras que esta skill garante

1. **`title:` sempre específico e humano.** Nunca ausente, nunca genérico ("README", "Index", "Notas").
   Título genérico torna a página indistinguível na busca e na árvore de navegação.
2. **Prefira link com caminho quando o nome puder ser ambíguo:** `[[pasta/nota]]` em vez de `[[nota]]`
   solto, principalmente em material importado em lote.
3. **Só marque `type: index` numa página que organiza outras de verdade** (um README com links). A marca
   diz às ferramentas de navegação que ali há um índice confiável.
4. **Atualize o índice da pasta** (`README.md`) ao criar uma página importante. Página sem índice
   apontando para ela é difícil de reencontrar.
5. **Frontmatter mínimo:** `title`, `tags`, `status` (`draft` | `active` | `deprecated`),
   `last_reviewed_at` (`YYYY-MM-DD`). Páginas canônicas levam `canonical: true`.

## O que não fazer

- Não faça `git add`, `commit` nem `push` no vault: o kernel guarda o histórico e sincroniza com o cérebro
  sozinho, a cada poucos minutos. Editar os arquivos basta.
- Não mova nem renomeie páginas com `mv` solto: ao renomear, reescreva os links que apontavam para ela e
  deixe o nome antigo em `aliases:` no frontmatter.
- Não coloque segredos, credenciais nem dados pessoais restritos no vault.

## Encerramento

Ao terminar uma sessão de escrita, resuma:

- arquivos afetados (caminhos sob o vault);
- classificação aplicada a cada um (projeto / domínio / recurso);
- índices atualizados.
