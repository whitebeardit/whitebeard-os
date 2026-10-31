# JARVIS OS — kernel instalável

Instala a arquitetura do JARVIS (Claude Code, skills, clientes MCP, Hermes) num host **zerado**, a partir de
uma declaração de tenant (`jarvis.yaml`), **sem carregar conhecimento nem dado privado de nenhuma empresa**.

Objetivo do projeto: pegar uma arquitetura de agentes que já roda em produção no dia a dia de uma empresa,
e transformá-la num **produto que outras pessoas baixam e instalam**. O primeiro usuário é a própria empresa
que o mantém (dogfooding); a estrutura já nasce de produto.

Este repositório é o **kernel**: mecanismo. Identidade (nomes, chats, contas, skills de negócio, segredos)
vive num **pacote de tenant** separado e privado, apontado na linha de comando. A regra que separa os dois:

> Mecanismo vai no kernel; identidade fica no tenant. Valor de tenant em código do kernel = falha de CI.

Três camadas:

| Camada | O que é | Onde vive | Exemplo |
| --- | --- | --- | --- |
| **kernel** | mecanismo obrigatório | este repositório | bootstrap, esquema do tenant, lint, skills de método |
| **packs** | mecanismo opcional, instalável | `packs/` (futuro) | CRM genérico, domínio fiscal BR, Instagram |
| **tenant** | identidade e dados | repositório privado do usuário | `jarvis.yaml`, skills de negócio, segredos |

## O pacote e onde cada parte roda

O pacote completo tem três papéis, e **por padrão os três são instalados na mesma máquina** (tudo-em-um).
Dividir em máquinas é opção, declarada em `nos:` no `jarvis.yaml`, não o padrão.

| Papel | Componentes | Precisa de |
| --- | --- | --- |
| **desktop** | Claude Code, skills, clientes MCP, JARVIS Web (UI no browser, servida localmente), Hermes CLI | só `$HOME` |
| **core** | Hermes (gateway Telegram + crons), Paperclip (Node + Postgres embutido) | host sempre ligado |
| **knowledge** | GBrain (MCP + ingestão do vault), pgvector, Ollama (embeddings), em Docker Compose | Docker (Linux) ou Docker Desktop/OrbStack (macOS) |

O mesmo `bootstrap.sh` instala qualquer combinação: `--role desktop,core,knowledge`. Um papel em outra
máquina é só outro host em `nos:` com os papéis dele; os MCPs e URLs do tenant apontam para lá.

A interface gráfica do produto é **uma só: JARVIS Web**, servida pelo próprio host e aberta no browser
(`http://localhost:8017`; na LAN ou no celular com TLS local). Não há app Electron no produto: mesma UI, sem
empacotamento nem assinatura por sistema operacional.

## Estado

Versão `0.1.0`. Papel implementado: **desktop** (Linux x86_64 e macOS). Os papéis `core` e `knowledge`
(mesmo host, compose) vêm na `0.2`; `satelite` (Hermes perfil + MCP em outra máquina) depois. Validação de
referência: CI em Ubuntu 24.04 e macOS, com o tenant fictício `tenants/exemplo`, que é também o ponto de
partida de um usuário novo.

## Uso rápido (host zerado)

```sh
git clone <este repositório> ~/DEV/jarvis-os
cd ~/DEV/jarvis-os
./bootstrap.sh --tenant tenants/exemplo/jarvis.yaml --dry-run   # só valida e mostra o plano
./bootstrap.sh --tenant tenants/exemplo/jarvis.yaml             # instala o papel desktop
```

Com um tenant real: `./bootstrap.sh --tenant /caminho/do/pacote-tenant/jarvis.yaml`. Opções em
`./bootstrap.sh --help` (`--with-hermes`, `--skip`, `--home`).

O bootstrap é idempotente e só escreve dentro de `$HOME`:

| Onde | O quê |
| --- | --- |
| `~/.jarvis/` (`$JARVIS_HOME`) | `jarvis.yaml` (cópia), `secrets.env` (modo 600), `profile.sh`, `render/`, `skills/` |
| `~/.local/bin/` | `uv`, `claude` |
| `~/.nvm/` | Node na versão do lockfile |
| `~/.claude/skills/<nome>` | links para `$JARVIS_HOME/skills/<nome>` |
| `~/.claude.json` | servidores MCP declarados no tenant (via `claude mcp add`) |
| `~/.zshrc` / `~/.bashrc` | uma linha: `. "$JARVIS_HOME/profile.sh"` |
| `~/.hermes/` | só com `--with-hermes` (instalador upstream, tag do lockfile) |

## Layout

```
bootstrap.sh                 entrada única (POSIX sh; Linux e macOS)
kernel/
  versions.env               lockfile: node, nvm, uv, Claude Code, Hermes
  schema/jarvis.schema.json  contrato do jarvis.yaml
  secrets/manifest.yaml      segredos do kernel: nome, papel, como obter (nunca valores)
  bin/jarvis_render.py       valida o tenant e gera secrets.env, profile.sh, MCP, skills
  bin/privacy_lint.py        falha se houver identidade de tenant, caminho de usuário ou segredo no kernel
  skills/                    skills do kernel (mecanismo puro)
tenants/exemplo/             tenant fictício: usado pelo CI e copiado por quem começa do zero
docs/validacao-macos.md      roteiro de validação no Mac zerado
test/docker-ubuntu.sh        mesma validação numa Ubuntu 24.04 descartável
```

## O que entra no `jarvis.yaml`

Parâmetros do tenant: identificador, idioma, vault, servidores MCP, pacotes de skills, modelo do Hermes,
perfis, crons, serviços monitorados, nós. Segredos **nunca** entram no yaml: entram como `${NOME}` e são
preenchidos em `secrets.env` a partir do manifesto. Esquema completo em `kernel/schema/jarvis.schema.json`;
exemplo em `tenants/exemplo/jarvis.yaml`.

## Componentes externos e licenças

| Componente | Licença | Como entra |
| --- | --- | --- |
| Hermes Agent (NousResearch) | MIT | instalador upstream em tag fixa |
| Paperclip | MIT | npm, versão fixa (papel core, próxima versão) |
| GBrain | MIT | compose (papel knowledge, próxima versão) |
| Claude Code | proprietário (Anthropic) | instalador oficial; cada usuário usa a própria conta |
| FFmpeg (série 9.0, com filtro Whisper) | LGPL/GPL conforme o build | build estático por SO/arquitetura baixado na instalação (0.2); câmera, áudio e transcrição |
| Modelos (Anthropic, OpenAI, outros) | contas do usuário | chaves e logins só em `secrets.env` e OAuth local |

## Regras do repositório

- Nenhum caminho absoluto de usuário, nome de cliente, chat id, conta de nuvem ou token no kernel.
  `uv run kernel/bin/privacy_lint.py` roda no CI; um pacote de tenant roda o mesmo lint com a própria lista
  de termos proibidos (`--denylist`), que não fica aqui.
- Versões só mudam em `kernel/versions.env`.
- Hosts em produção não são alterados por este repositório. A instalação se prova primeiro em host zerado
  (CI, VM descartável, máquina nova).
