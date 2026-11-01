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
