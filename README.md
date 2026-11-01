# Whitebeard OS — kernel instalável

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
| **desktop** | Claude Code, skills, clientes MCP, JARVIS Web (UI no browser, construída do app e registrada como serviço de usuário), Hermes CLI | só `$HOME` e chave SSH com acesso ao repositório do app |
| **core** | Hermes (gateway Telegram + crons), Paperclip (Node + Postgres embutido) | host sempre ligado |
| **knowledge** | GBrain (MCP + ingestão do vault), pgvector, Ollama (embeddings), em Docker Compose | Docker (Linux) ou Docker Desktop/OrbStack (macOS) |

Sem `--role`, o `bootstrap.sh` instala os papéis do host `local` em `nos:` do tenant (sem `nos:`, os três:
tudo-em-um). Um papel em outra máquina é só outro host em `nos:` com os papéis dele; os MCPs e URLs do
tenant apontam para lá. Segredos obrigatórios que faltam são pedidos no terminal (`--no-prompt` desliga).
Sem Docker, o papel knowledge é adiado com aviso e os outros seguem.

**Camada de decisão com provedor plugável.** Rotear mensagens, classificar intents e aplicar guardrails são
perguntas fechadas, e o kernel as trata por uma abstração própria: perguntas tipadas versionadas, limiar de
confiança e escalada para o LLM ou para o humano. O tenant escolhe o provedor em `decisoes.provider`:
`regras` (determinístico, padrão), `llm` (modelos já configurados) ou `jev` (modelo de decisão da TypeSafe
AI, por API, com `TYPESAFE_API_KEY`). Nenhum provedor externo é obrigatório, e `perfis_excluidos` impede
que conteúdo de certos perfis saia da máquina.

**GPU é opcional, nunca obrigatória.** `aceleracao: auto` no `jarvis.yaml` detecta CUDA (Linux/NVIDIA),
Metal (Apple Silicon) ou Vulkan na instalação e cai para CPU se não houver. Onde a GPU entra: transcrição
(Whisper), embeddings e modelos locais (Ollama), vídeo das câmeras (hwaccel do FFmpeg). No Linux, o papel
knowledge em compose usa a GPU por CDI/NVIDIA Container Toolkit; no macOS, containers não veem a GPU, então
Ollama e Whisper rodam nativos e o compose aponta para eles.

A interface gráfica do produto é **uma só: JARVIS Web**, servida pelo próprio host e aberta no browser
(`http://localhost:8017`; na LAN ou no celular com TLS local). Não há app Electron no produto: mesma UI, sem
empacotamento nem assinatura por sistema operacional. O app lê as pastas pelo mesmo contrato do `profile.sh`
(`JARVIS_HOME`, `JARVIS_DATA`, `HERMES_HOME`, `VAULT_PATH`), sem nome de tenant no código.

## Estado

Versão `0.2.0-dev`. Os três papéis instalam a partir do mesmo bootstrap:

- **desktop**: provado em CI (Ubuntu 24.04 e macOS) e em Ubuntu descartável; aguardando validação no Mac físico.
- **core**: Paperclip fixado no lockfile e instalado pelo npm do Node 24; Hermes upstream instalado por
  padrão; serviços de usuário registrados pelos próprios CLIs (`paperclipai onboard --install-service`,
  `hermes gateway install`). O CI prova instalação e renderização (`--no-start`); subir os serviços se prova
  no Mac físico.
- **knowledge**: compose com GBrain (tag fixa), pgvector e Ollama, portas só em localhost, senha e token
  gerados na instalação. O CI valida o compose e sobe o banco até ficar saudável.

`satelite` (Hermes perfil + MCP em outra máquina) vem a seguir; o JARVIS Web já entra no papel desktop,
com TLS local, a partir do branch do app fixado no lockfile. Validação de
referência: tenant fictício `tenants/exemplo`, que é também o ponto de partida de um usuário novo.

## Instalação em um comando (host zerado)

```sh
curl -fsSL https://raw.githubusercontent.com/whitebeardit/whitebeard-os/main/install.sh | sh
```

O `install.sh` garante git e curl (no macOS instala as Command Line Tools), clona o kernel em
`~/DEV/whitebeard-os` e roda o `bootstrap.sh` com o tenant de exemplo. Opções passam direto:
`| sh -s -- --with-hermes`. Tenant real: `JARVIS_TENANT=<nome, pasta ou /caminho/jarvis.yaml>` antes do comando.

Enquanto o repositório for **privado**, o `curl` anônimo não alcança o arquivo. Com a chave SSH cadastrada
no GitHub, o equivalente é:

```sh
git clone git@github.com:whitebeardit/whitebeard-os.git ~/DEV/whitebeard-os && ~/DEV/whitebeard-os/install.sh
```

Passo a passo, sem o instalador:

```sh
cd ~/DEV/whitebeard-os
./bootstrap.sh --tenant tenants/exemplo/jarvis.yaml --dry-run                   # só valida e mostra o plano
./bootstrap.sh --tenant tenants/exemplo/jarvis.yaml                             # papel desktop
./bootstrap.sh --tenant tenants/exemplo/jarvis.yaml --role desktop,core,knowledge   # tudo-em-um
```

Opções em `./bootstrap.sh --help` (`--role`, `--no-start`, `--with-hermes`, `--skip`, `--home`). O CI roda com
`--skip web` porque o repositório do app é privado e o runner não tem a chave; num host seu, o JARVIS Web
entra no papel desktop e sobe em `https://localhost:8017` (token em `$JARVIS_DATA/jarvis-web.json`; certificado
autoassinado em `~/.jarvis/tls`, que o celular instala para liberar o microfone).

O bootstrap é idempotente e só escreve dentro de `$HOME`:

| Onde | O quê | Papel |
| --- | --- | --- |
| `~/.jarvis/` (`$JARVIS_HOME`) | `jarvis.yaml` (cópia), `secrets.env` (600), `profile.sh`, `render/`, `skills/`, `tools/ffmpeg-*`, `compose/knowledge/` | todos |
| `$JARVIS_DATA` (`dados:` do tenant, padrão `~/.jarvis/data`) | dados do tenant: posts, métricas, contratos, registros e token do JARVIS Web | todos |
| `~/.local/bin/` | `uv`, `claude`, `ffmpeg`, `ffprobe` | todos |
| `~/.jarvis/app/jarvis-web/` | app JARVIS Web na ref do lockfile, construído; serviço `jarvis-web` (systemd de usuário) ou LaunchAgent `dev.whitebeard.jarvis-web` lendo `profile.env` | desktop |
| `~/.jarvis/tls/` | certificado autoassinado do JARVIS Web (SAN: localhost, host, IPs); o serviço sobe em HTTPS | desktop |
| `~/.nvm/` | Node na versão do lockfile; `paperclipai` instalado nele | todos / core |
| `~/.claude/skills/<nome>`, `~/.claude.json` | links para as skills; servidores MCP do tenant | desktop |
| `~/.hermes/` | Hermes upstream (tag fixa); `config.yaml` inicial e `.env` do tenant, sem sobrescrever | core (ou `--with-hermes`) |
| `~/.paperclip/` | instância do Paperclip (Postgres embutido) criada por `paperclipai onboard` | core |
| Docker | projeto `whitebeard-knowledge`: GBrain, pgvector, Ollama, volumes próprios | knowledge |
| `~/.zshrc` / `~/.bashrc` | uma linha: `. "$JARVIS_HOME/profile.sh"` | todos |

## Layout

```
bootstrap.sh                 entrada única (POSIX sh; Linux e macOS); --role desktop,core,knowledge
install.sh                   instalação em um comando (clona o kernel e chama o bootstrap)
kernel/
  versions.env               lockfile: node, nvm, uv, Claude Code, Hermes, Paperclip, GBrain, imagens, FFmpeg
  schema/jarvis.schema.json  contrato do jarvis.yaml
  secrets/manifest.yaml      segredos do kernel: nome, papel, como obter (nunca valores)
  bin/jarvis_render.py       valida o tenant e gera secrets.env, profile.sh, MCP, skills, hermes/, knowledge.env
  bin/privacy_lint.py        falha se houver identidade de tenant, caminho de usuário ou segredo no kernel
  compose/knowledge/         GBrain + pgvector + Ollama (compose, Dockerfile, entrypoint, sobreposição GPU)
  skills/                    skills do kernel (mecanismo puro)
tenants/exemplo/             tenant fictício: usado pelo CI e copiado por quem começa do zero
docs/validacao-macos.md      roteiro de validação no Mac zerado
docs/validacao-macos-agente.md  o mesmo roteiro, escrito para o Claude Code do próprio Mac executar
test/docker-ubuntu.sh        mesma validação numa Ubuntu 24.04 descartável
```

## Levar tudo para outra máquina: exportar e importar

```sh
uv run kernel/bin/wb_export.py --dry-run                 # lista stores e tamanhos
uv run kernel/bin/wb_export.py --sem financeiro          # bundle .tar.gz (modo 600) + .sha256, sem o grupo "financeiro" do tenant
# na máquina nova, depois do bootstrap com o mesmo tenant:
uv run kernel/bin/wb_import.py whitebeard-export-*.tar.gz
```

Stores: `jarvis` (`$JARVIS_HOME` sem o que é regenerável), `dados` (`$JARVIS_DATA`, sem os tokens por máquina), `hermes` (sem código, caches e tokens por máquina),
`paperclip` (config, arquivos e o backup de banco mais recente), `knowledge` (dump do Postgres e home do GBrain,
se o compose estiver no ar), `claude` (configurações) e `vault` (só referência ao git, ou conteúdo com
`--with-vault`). Segredos ficam de fora por padrão (`--with-secrets` para incluir); o import nunca sobrescreve
`secrets.env` nem `.env` existentes. O tenant declara em `exportacao.grupos` os globs que podem ficar de fora
(ex.: financeiro). O CI exporta e importa num HOME alternativo a cada execução.

## O que entra no `jarvis.yaml`

Parâmetros do tenant: identificador, idioma, pasta de dados (`dados`), módulos do JARVIS Web (`interface`),
vault, servidores MCP, pacotes de skills, modelo do Hermes, perfis, crons, serviços monitorados, nós. Segredos **nunca** entram no yaml: entram como `${NOME}` e são
preenchidos em `secrets.env` a partir do manifesto. Esquema completo em `kernel/schema/jarvis.schema.json`;
exemplo em `tenants/exemplo/jarvis.yaml`.

## Componentes externos e licenças

| Componente | Licença | Como entra |
| --- | --- | --- |
| Hermes Agent (NousResearch) | MIT | instalador upstream em tag fixa |
| Paperclip | MIT | npm, versão fixa, instalado no Node do lockfile (papel core) |
| GBrain, pgvector, Ollama | MIT (GBrain), PostgreSQL, MIT (Ollama) | compose do papel knowledge; GBrain construído da tag fixa |
| Claude Code | proprietário (Anthropic) | instalador oficial; cada usuário usa a própria conta |
| FFmpeg 9.0 | LGPL | build próprio com o filtro Whisper (whisper.cpp), publicado em [whitebeard-os-releases](https://github.com/whitebeardit/whitebeard-os-releases) com sha256: macOS arm64 com GPU Metal e avfoundation (câmera e microfone), Linux x86_64 estático em CPU (workflow `ffmpeg-whisper.yml`); demais plataformas: build estático de terceiros fixado por URL, sem Whisper |
| Modelos (Anthropic, OpenAI, outros) | contas do usuário | chaves e logins só em `secrets.env` e OAuth local |

## Licenciamento do produto

Modelo decidido em 31/10/2026: **licença única, para baixar e usar**. Pagamento único por chave; a chave dá
direito a baixar e instalar o kernel e suas atualizações, sem prazo, nas máquinas do próprio comprador. Sem
assinatura, sem SLA de suporte, sem redistribuição do kernel. Hermes, Paperclip e GBrain continuam sob as
licenças MIT do upstream e não fazem parte da licença. O texto legal dos termos de uso ainda será
redigido; enquanto o repositório é privado, o acesso é por convite de leitura.

## Regras do repositório

- Nenhum caminho absoluto de usuário, nome de cliente, chat id, conta de nuvem ou token no kernel.
  `uv run kernel/bin/privacy_lint.py` roda no CI; um pacote de tenant roda o mesmo lint com a própria lista
  de termos proibidos (`--denylist`), que não fica aqui.
- Versões só mudam em `kernel/versions.env`.
- Hosts em produção não são alterados por este repositório. A instalação se prova primeiro em host zerado
  (CI, VM descartável, máquina nova).
