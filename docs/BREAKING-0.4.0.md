# Whitebeard OS 0.4.0: nomes em inglês (breaking change)

A partir da 0.4.0, todo nome que o Whitebeard OS lê ou grava está em inglês: chaves do `jarvis.yaml` e do
`addon.yaml`, valores de enum, arquivos de estado, subcomandos, ferramentas dos MCPs, variáveis de ambiente,
serviços, skills e tenants. Texto para pessoas (mensagens, documentação, comentários, prompts) continua em
português do Brasil, e a interface continua com os dois idiomas.

**Não há compatibilidade com os nomes antigos.** O Whitebeard OS ainda está em teste: quem instalou antes da
0.4.0 é tester e precisa migrar a configuração (seção [Como migrar](#como-migrar)). Um `jarvis.yaml` com as chaves
antigas é recusado na validação, com a lista das chaves desconhecidas.

## Tenants

| Antes | Depois |
| --- | --- |
| `tenants/exemplo/` (`tenant: exemplo`) | `tenants/example/` (`tenant: example`) |
| `tenants/pessoal/` (`tenant: pessoal`) | `tenants/personal/` (`tenant: personal`) |
| `tenants/exemplo/addon-exemplo/` | `tenants/example/addon-example/` |
| skill `acompanhamento-alunos` (tenant pessoal) | removida (o add-on Mastery traz a `mastery-grading`) |

O nome do tenant também é a pasta dele no vault: `tenants/pessoal/` vira `tenants/personal/`.

## `jarvis.yaml`: chaves

| Antes | Depois |
| --- | --- |
| `idioma` | `language` |
| `aceleracao` | `acceleration` |
| `dados` | `data` |
| `interface.modulos` | `interface.modules` |
| `segredos` | `secrets` |
| `decisoes` | `decisions` |
| `perfis` | `profiles` |
| `perfis_excluidos` | `excluded_profiles` |
| `servicos_monitorados` | `monitored_services` |
| `nos` | `nodes` |
| `papel` / `papeis` | `role` / `roles` |
| `assistentes` | `assistants` |
| `escritorio` | `office` |
| `cargos` / `cargo` | `positions` / `position` |
| `rotinas` | `routines` |
| `exportacao` | `export` |
| `nome` | `name` |
| `descricao` | `description` |
| `tipo` | `type` |
| `canal` | `channel` |
| `ajustes` | `settings` |
| `ajustes.computador` | `settings.computer` |
| `ajustes.anotar` | `settings.write_notes` |
| `nivel` | `level` |
| `provedor` | `provider` |
| `modelo` | `model` |
| `whatsapp_modo` | `whatsapp_mode` |
| `perfil` | `profile` |
| `quando` | `when` |
| `agenda` | `schedule` |
| `faz` | `does` |
| `titulo` | `title` |
| `onde` | `where` |
| `ligado` / `ligada` | `enabled` |
| `grupos` | `groups` |
| `limiar_confianca` | `confidence_threshold` |

Chaves que já eram em inglês (`tenant`, `interface`, `vault`, `mcp`, `skills`, `packs`, `hermes`, `telegram`,
`crons`, `claude`, `plugins`, `marketplaces`, `host`, `name`, `command`, `args`, `env`, `url`, `headers`,
`transport`, `roles`, `id`, `repo`, `prompt`, `skill`, `bot`, `chat_id`, `home_chat`, `path`, `provider`,
`model`, `fallback`, `aws_profile`) ficam como estão.

## `addon.yaml`

| Antes | Depois |
| --- | --- |
| `descricao` | `description` |
| `segredos` | `secrets` |

## Valores de enum

| Campo | Antes | Depois |
| --- | --- | --- |
| `interface.modules` | `sistemas`, `conhecimento`, `arquivos`, `comunicacoes`, `ferramentas`, `diagnosticos`, `operacao`, `midias`, `rastreamento` | `systems`, `knowledge`, `files`, `communications`, `tools`, `diagnostics`, `operations`, `media`, `tracking` (`camera`, `instagram`, `leads`, `analytics` ficam) |
| `decisions.provider` | `regras` | `rules` (`llm`, `jev` ficam) |
| `assistants[].type` | `mim`, `equipe`, `cliente`, `rotinas` | `owner`, `team`, `client`, `routines` |
| `level` (assistente, cargo, hermes) | `rapido`, `equilibrado`, `capaz` | `fast`, `balanced`, `capable` |
| `profiles[].type` | `pessoal`, `grupo-cliente`, `grupo-interno`, `servico` | `personal`, `client-group`, `internal-group`, `service` |
| `nodes[].roles` | `satelite` | `satellite` (`desktop`, `core`, `knowledge` ficam) |
| cargos padrão do escritório | `trabalho`, `coordenacao` | `work`, `coordination` |

## Arquivos de estado (`$JARVIS_HOME` e `$JARVIS_DATA`)

| Antes | Depois |
| --- | --- |
| `assistentes.json`, `assistentes.applied` | `assistants.json`, `assistants.applied` |
| `escritorio.json` | `office.json` |
| `cargo.json`, `cargos.json` | `position.json`, `positions.json` |
| `rotinas.json` | `routines.json` |
| `carteiro.json` | `courier.json` |
| `pensa.json`, `pensa.cache.json`, `pensa.login.json` | `thinker.json`, `thinker.cache.json`, `thinker.login.json` |
| `telegram.decisao` | `telegram.decision` |
| `preferencias.json` (`$JARVIS_DATA`) | `preferences.json` |
| `render/hermes/modelo.yaml` | `render/hermes/model.yaml` |
| `render/hermes/cargos/` | `render/hermes/positions/` |
| `kernel/modelos.yaml` | `kernel/models.yaml` |

As chaves dentro desses arquivos seguem a tabela do `jarvis.yaml` (por exemplo, `assistants.json` grava `name`,
`type`, `channel`, `description`, `skills`, `settings`, `level`, `provider`, `whatsapp_mode`, `created_at`,
`bot`, `app_id`, `people_names`, `group_names`, `removed`, `from_pack`).

## Scripts do kernel (`kernel/bin/`)

| Antes | Depois |
| --- | --- |
| `jarvis_assistente.py` | `jarvis_assistant.py` |
| `jarvis_carteiro.py` | `jarvis_courier.py` |
| `jarvis_cerebro_mcp.py` | `jarvis_vault_mcp.py` |
| `jarvis_escritorio.py` | `jarvis_office.py` |
| `jarvis_escritorio_mcp.py` | `jarvis_office_mcp.py` |
| `jarvis_escritorio_tokens.py` | `jarvis_office_tokens.py` |
| `jarvis_pensa.py` | `jarvis_thinker.py` |
| `jarvis_rotinas.py` | `jarvis_routines.py` |
| `kernel/i18n/en/servicos.json` | `kernel/i18n/en/services.json` |

## Subcomandos do `jarvis_setup.py` (usados pelo JARVIS Web)

| Antes | Depois |
| --- | --- |
| `assistente-add`, `assistente-update`, `assistente-remove` | `assistant-add`, `assistant-update`, `assistant-remove` |
| `assistente-token`, `assistente-wait`, `assistente-esquecer` | `assistant-token`, `assistant-wait`, `assistant-forget` |
| `escritorio-set`, `escritorio-cargo-add`, `escritorio-rotina-add` | `office-set`, `office-position-add`, `office-routine-add` |
| `pensa-set`, `pensa-login-status`, `pensa-login-cancel` | `thinker-set`, `thinker-login-status`, `thinker-login-cancel` |

`status`, `done`, `set-secret`, `telegram-*` e `update-*` ficam. O JSON que eles devolvem usa as chaves em inglês
(por exemplo, `assistants`, `people`, `groups`, `secrets`, `office`, `positions`, `routines`, `thinker`, `error`,
`state`, `pending`, `available`).

Opção `--desligar` (escritório) vira `--disable`.

## MCPs do kernel

| Antes | Depois |
| --- | --- |
| MCP `cerebro`: `ler_nota`, `escrever_nota`, `editar_nota` | MCP `vault`: `read_note`, `write_note`, `edit_note` |
| MCP `escritorio`: `escritorio`, `ambiente`, `criar_tarefa`, `minhas_tarefas`, `aprovar`, `recusar`, `comentar`, `entregar`, `pedir_aprovacao` | MCP `office`: `office`, `environment`, `create_task`, `my_tasks`, `approve`, `reject`, `comment`, `deliver`, `request_approval` |
| MCP `github`: `listar_repositorio`, `ler_arquivo` | MCP `github`: `list_repository`, `read_file` |

Nomes reservados para MCP de tenant ou add-on: `knowledge`, `vault`, `github`, `office`. Os argumentos das
ferramentas também ficam em inglês (`caminho` → `path`, `conteudo` → `content`, `trecho` → `old_text`, etc.).

## Variáveis de ambiente e segredos

| Antes | Depois |
| --- | --- |
| `GITHUB_LEITURA_TOKEN` | `GITHUB_READ_TOKEN` |
| `JARVIS_IDIOMA` | `JARVIS_LANGUAGE` |
| `WHITEBEARD_ASSISTENTE`, `WHITEBEARD_ASSISTENTE_TIPO` | `WHITEBEARD_ASSISTANT`, `WHITEBEARD_ASSISTANT_TYPE` |
| `WHITEBEARD_CARGO` | `WHITEBEARD_POSITION` |
| `ESCRITORIO_CARGO_ID`, `ESCRITORIO_CARGO_NOME`, `ESCRITORIO_ORIGEM` | `OFFICE_POSITION_ID`, `OFFICE_POSITION_NAME`, `OFFICE_ORIGIN` |

Os segredos de cada assistente (`DISCORD_BOT_TOKEN_<NOME>` e afins) e os demais nomes já em inglês ficam.

## Serviços

| Antes | Depois |
| --- | --- |
| `whitebeard-carteiro.service` / `.timer` (Linux) | `whitebeard-courier.service` / `.timer` |
| `dev.whitebeard.carteiro` (macOS) | `dev.whitebeard.courier` |

## Nomes definidos na implementação

- `hermes.provedor` → `hermes.subscription` (o bloco `hermes:` já tinha `provider`, o provedor do Hermes);
  `hermes.nivel` → `hermes.level`. Nos assistentes e no `thinker.json`, `provedor` → `provider`.
- `ajustes` do assistente → `settings` (`computador` → `computer`, `anotar` → `write_notes`).
- Pastas de estado: `$JARVIS_HOME/escritorio/` → `office/` (`positions.json`, `position.json`, `routines.json`,
  `courier.json`), `$JARVIS_HOME/assistentes/` → `assistants/` (sessões do WhatsApp),
  `render/hermes/assistentes/` → `render/hermes/assistants/`; logs `carteiro.log` → `courier.log`,
  `pensa-login.log` → `thinker-login.log`, `instalacao-*.log` → `install-*.log`.
- Vault: `projects-doc/escritorio/entregas/` → `projects-doc/office/deliveries/`.
- `render/knowledge.env`: `ACELERACAO` → `ACCELERATION`.
- Subcomandos a mais: `assistant-whatsapp-pair`/`-status`/`-unpair`, `office-approve`/`office-reject`,
  `office-position-update`/`-remove`, `office-routine-update`/`-remove`/`-run`, `thinker-login`/`-logout`/`-key`.
- Valores: estado do WhatsApp `preparing`, `waiting`, `qr`, `connected`, `error`, `expired`, `cancelled`, `none`;
  alertas de tokens `costly-routine`, `costly-task`, `position-doubled`, `stuck-task`, `position-failing`;
  entrada do provedor `claude-code`, `code`, `key`; `where: assistant:<nome>`.
- Marcadores do Paperclip: `ENTREGA:`, `PENDÊNCIA:`, `RECUSADO`, `APROVADO` → `DELIVERY:`, `PENDING:`, `REJECTED`,
  `APPROVED`; rodapés `[origem:]`, `[rotina:]` → `[origin:]`, `[routine:]`. Tarefas antigas ficam com os antigos.
- `wb_export.py --sem` → `--without`; no pacote exportado, `dados` → `data`.
- Tenant de exemplo: `example_tools`, `EXAMPLE_TOKEN`, add-on `example-addon` (`hello_mcp.py`, skill
  `example-addon-hello`), skill `example-hello`, assistentes `support`, `example-client`, `discord-example`,
  `whatsapp-example`, cargo `writer`, rotinas `daily-status` e `weekly-summary` (padrão), dados em `.example`.
- CI: workflow `install.yml` (`clean-install`), job `install`.

O conjunto completo das chaves de saída do `jarvis_setup.py` está no próprio código; quem consome (o JARVIS Web)
foi ajustado junto.

## Como migrar

1. Atualize o kernel (`git pull` na pasta do whitebeard-os).
2. Rode `python3 kernel/bin/migrate_0_4.py --home ~/.jarvis --tenant <seu jarvis.yaml>`: renomeia os arquivos de
   estado e as chaves dentro deles, e reescreve o `jarvis.yaml` com as chaves novas (guarda uma cópia `.bak`).
   Tenant próprio em outro repositório: o mesmo comando, apontando para o `jarvis.yaml` dele.
3. Se o seu tenant era `pessoal` ou `exemplo`, aponte para `tenants/personal/jarvis.yaml` ou
   `tenants/example/jarvis.yaml` e mova a pasta do tenant no vault (`tenants/pessoal/` → `tenants/personal/`).
4. `./bootstrap.sh --apply` (as conversas abertas dos assistentes são encerradas sozinhas quando as skills ou os MCPs mudam).
5. Skills próprias que citam ferramentas do kernel (`ler_nota`, `listar_repositorio`…) precisam dos nomes novos.
