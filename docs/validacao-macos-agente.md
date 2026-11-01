# Validação no Mac conduzida pelo Claude Code local

Este documento é uma instrução para o **Claude Code instalado no próprio Mac** pelo papel desktop. O fundador
abre o Claude Code em `~/DEV/whitebeard-os` e pede: "leia `docs/validacao-macos-agente.md` e execute". O
agente conduz a validação, pede ao fundador só o que é interativo e termina com um relatório em arquivo.

## Papel e limites

- Você está num Mac com Apple Silicon onde o Whitebeard OS já foi instalado pelo menos uma vez (tudo-em-um:
  papéis desktop, core e knowledge; o knowledge ficou adiado por falta de Docker).
- Rode tudo como o usuário normal. **Nunca use `sudo`**, nunca altere nada fora de `$HOME`, nunca mexa em
  ajustes do sistema. Instalar aplicativos (OrbStack) é tarefa do fundador.
- **Segredos nunca aparecem na conversa nem no relatório.** Não rode `cat` em `~/.jarvis/secrets.env`,
  `~/.hermes/.env` ou `~/.hermes/auth.json`. Para saber se um campo está preenchido use
  `grep -c '^NOME=.' arquivo` (1 = preenchido, 0 = vazio). Nunca peça ao fundador para colar uma chave no
  chat: peça que ele edite o arquivo com `nano` e avise quando terminar.
- Nunca use o bot de produção do Telegram. Só o bot de teste que o fundador criar.
- Não faça `git commit` nem `git push` em `~/DEV/whitebeard-os`. Se um passo quebrar por bug do kernel, pode
  fazer a correção local mínima para destravar, mas registre o `git diff` no relatório: a correção definitiva
  entra pelo repositório. Nenhuma correção pode envolver segredo.
- Passos interativos (login OAuth, senha, permissão de microfone, olhar a tela) são do fundador. Diga
  exatamente o que ele deve fazer, espere a confirmação e siga.
- Não repita uma ação que falhou mais de duas vezes. Registre o erro e passe ao próximo passo.

## Causas já conhecidas (não investigue de novo)

- "spawn …/hermes ENOENT", voz (STT) indisponível e painel de MCPs vazio no JARVIS Web tinham a mesma causa:
  o Hermes foi instalado antes de uma correção e o venv ficou em `~/DEV/whitebeard-os/venv` em vez de
  `~/.hermes/hermes-agent/venv`. A reinstalação com o kernel atual corrige. O venv do Hermes já traz
  `faster-whisper` e `edge-tts` (extra `all` do instalador upstream).
- O tenant de exemplo esconde Instagram, Mídias, Analytics, Leads e Rastreamento de propósito (módulos por
  tenant). Só Sistemas, Conhecimento, Arquivos, Comunicações, Ferramentas, Diagnósticos e Câmera aparecem.
- `ANTHROPIC_API_KEY` é opcional. O provedor do modelo é escolhido com `hermes model` (assinatura do ChatGPT,
  Nous Portal, OpenRouter ou Claude Max por OAuth). `hermes model` é interativo: pedir ao fundador.
- `edge-tts` pode responder 403 (serviço externo); o modelo `ggml-small.bin` vem de `ggerganov/whisper.cpp`
  (o espelho `ggml-org` devolve 401). Não gaste tempo nisso.
- O app ainda transcreve com o daemon `faster-whisper` (CPU). O FFmpeg com o filtro `whisper` em Metal é uma
  prova separada do caminho futuro, não substitui nada hoje.

## Relatório

Crie `~/DEV/validacao-mac-AAAA-MM-DD.md` logo no início e vá preenchendo a cada passo, com esta estrutura:
`sw_vers` e `uname -m`; para cada passo, o comando, o resultado resumido (sem segredos), `ok` / `falhou` /
`pulado` e o motivo; ao final, lista de bugs do kernel ou do app com a mensagem de erro exata e a correção
sugerida, e a lista do que ficou pendente com o fundador. Ao terminar, mostre o relatório inteiro na conversa:
o fundador vai copiá-lo para o Claude Code da socrates, que fecha as pendências no vault, no artefato e na
WHI-152.

## Passo 1: segredos e reinstalação

1. Confira os campos, sem mostrar valores:

   ```sh
   for k in TELEGRAM_BOT_TOKEN TELEGRAM_HOME_CHANNEL ANTHROPIC_API_KEY PAPERCLIP_API_KEY; do
     printf '%s=%s\n' "$k" "$(grep -c "^$k=." ~/.jarvis/secrets.env)"
   done
   ```

2. Se `TELEGRAM_BOT_TOKEN` ou `TELEGRAM_HOME_CHANNEL` estiver em 0, peça ao fundador: criar um bot de teste no
   BotFather, mandar "oi" para o bot, descobrir o id do chat com
   `curl -s "https://api.telegram.org/bot<TOKEN>/getUpdates" | python3 -m json.tool | grep -m1 '"id"'`
   (ele roda isso no terminal dele) e preencher as duas linhas com `nano ~/.jarvis/secrets.env`. Espere a
   confirmação. `ANTHROPIC_API_KEY` pode ficar vazio.
3. Reinstale. O instalador atualiza o kernel sozinho, é idempotente e reinicia o JARVIS Web. Sem terminal
   interativo ele não pergunta segredos, só avisa; por isso o passo 2 vem antes.

   ```sh
   cd ~/DEV/whitebeard-os && ./install.sh 2>&1 | tee ~/DEV/install-mac.log | tail -n 40
   ```

   Esperado no bloco `---- resumo ----`: node, uv, claude, hermes, paperclip e jarvis-web presentes;
   0 segredos obrigatórios faltando; aviso de knowledge adiado (sem Docker). Copie o bloco de resumo para o
   relatório. Se parar com `die`, registre a mensagem exata e a última tela de `~/DEV/install-mac.log`.

## Passo 2: papel core

Num shell novo (ou com `source ~/.jarvis/profile.sh`), rode e registre cada saída:

```sh
hermes --version
ls -l ~/.local/bin/hermes
~/.hermes/hermes-agent/venv/bin/python3 -c "import faster_whisper, edge_tts; print('voz ok')"
hermes mcp list
hermes gateway status
paperclipai service status
```

Esperado: o link `~/.local/bin/hermes` aponta para `~/.hermes/hermes-agent/venv/bin/hermes`; "voz ok";
MCPs `exemplo_knowledge` e `exemplo_ferramentas` listados; gateway e Paperclip rodando.

Provedor do modelo: peça ao fundador para rodar `hermes model` no terminal dele, escolher "ChatGPT or Codex
Subscription" (ou outro provedor que ele tenha) e concluir o login pelo código. Espere a confirmação. Depois:

```sh
hermes chat -Q -q "responda só a palavra: oi"
```

Esperado: uma resposta curta do modelo. Se o gateway estava parado antes do login, rode
`hermes gateway restart` (ou `stop` e `start`) e confira `hermes gateway status` de novo.

Prova do Telegram: peça ao fundador para mandar "oi" ao bot de teste e dizer se respondeu. Registre a resposta
dele. Se não respondeu, anexe as últimas 40 linhas do log do gateway (`hermes gateway status` mostra o
caminho; se não mostrar, procure em `~/.hermes/logs/`).

## Passo 3: JARVIS Web

```sh
launchctl print "gui/$(id -u)/dev.whitebeard.jarvis-web" | head -n 5
tail -n 20 ~/.jarvis/logs/jarvis-web.log
TOKEN=$(python3 -c 'import json,os;print(json.load(open(os.environ["JARVIS_DATA"]+"/jarvis-web.json"))["token"])')
curl -sk -o /dev/null -w 'ui http=%{http_code}\n' "https://localhost:8017/?token=$TOKEN"
curl -sk -H "Authorization: Bearer $TOKEN" https://localhost:8017/api/paths
curl -sk -H "Authorization: Bearer $TOKEN" https://localhost:8017/api/config
```

Esperado: LaunchAgent ativo, `ui http=200`, `/api/paths` com caminhos dentro de `$HOME` e `/api/config` com
`tenant: exemplo` e a lista de módulos genéricos. Depois abra a UI para o fundador:

```sh
open "https://localhost:8017/?token=$TOKEN"
```

Peça que ele dê um reload forçado (Cmd+Shift+R) e descreva: quais menus aparecem; se CPU, memória e disco
estão preenchidos; se os MCPs aparecem; se Hermes e Paperclip estão "on"; se "oi" no chat responde; se o
microfone transcreve. Registre a descrição dele. Para qualquer painel com erro, anexe as linhas
correspondentes de `~/.jarvis/logs/jarvis-web.log`.

## Passo 4: FFmpeg com Whisper em Metal (demorado, roda em paralelo)

Inicie o build logo depois do passo 1 e siga com os passos 2 e 3 enquanto ele roda (30 a 60 minutos):

```sh
git clone --depth 1 --branch v9.0.10 https://github.com/markus-perl/ffmpeg-build-script.git ~/DEV/ffmpeg-build-script
cd ~/DEV/ffmpeg-build-script && SKIPINSTALL=yes nohup ./build-ffmpeg --build --whisper=metal --disable=rav1e > ~/DEV/ffmpeg-build.log 2>&1 &
```

Quando terminar (`tail -n 5 ~/DEV/ffmpeg-build.log`):

```sh
F=~/DEV/ffmpeg-build-script/workspace/bin/ffmpeg
$F -hide_banner -filters | grep whisper
ls -l ~/DEV/ggml-small.bin
```

Se `ggml-small.bin` não existir:

```sh
curl -L -o ~/DEV/ggml-small.bin https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin
```

Gravação real: avise o fundador que o macOS vai pedir permissão de microfone para o terminal e que ele deve
falar uma frase em português por 6 segundos. Liste os dispositivos e grave:

```sh
$F -f avfoundation -list_devices true -i "" 2>&1 | grep -A5 "audio devices"
$F -y -f avfoundation -i ":0" -t 6 ~/DEV/voz.wav
```

Transcreva com a GPU (Metal) e meça o tempo:

```sh
time $F -hide_banner -i ~/DEV/voz.wav -af "whisper=model=$HOME/DEV/ggml-small.bin:language=pt:queue=10:destination=$HOME/DEV/voz.txt:format=text" -f null -
cat ~/DEV/voz.txt
```

Registre o texto transcrito, o tempo e se o log do FFmpeg mencionou Metal. Se o build falhar, registre as
últimas 60 linhas de `~/DEV/ffmpeg-build.log` e siga; se o microfone falhar, use
`say -o ~/DEV/voz.aiff "testando o whisper no mac"` como entrada, marcando no relatório que foi voz sintética.

## Passo 5: Docker e papel knowledge

```sh
docker version 2>&1 | head -n 3
```

Se não houver Docker, peça ao fundador para instalar o OrbStack (https://orbstack.dev/download, arrastar para
Aplicativos e abrir uma vez) e avisar. Depois:

```sh
docker version | head -n 3 && cd ~/DEV/whitebeard-os && ./install.sh 2>&1 | tail -n 25
docker ps --format '{{.Names}}  {{.Status}}'
curl -s -o /dev/null -w 'gbrain http=%{http_code}\n' http://localhost:3131/
```

Esperado: GBrain, pgvector e Ollama em execução e a porta 3131 respondendo. Se o fundador preferir não
instalar Docker agora, marque como pulado por decisão dele.

## Passo 6: Paperclip

```sh
paperclipai auth bootstrap-ceo
```

Entregue ao fundador o link ou as instruções que o comando imprimir e peça que ele entre em
`http://localhost:3100`, vá em Settings, API keys, crie uma chave e a coloque em `~/.jarvis/secrets.env` na
linha `PAPERCLIP_API_KEY=` (ele edita, você não vê). Quando ele confirmar:

```sh
grep -c '^PAPERCLIP_API_KEY=.' ~/.jarvis/secrets.env
cd ~/DEV/whitebeard-os && ./install.sh 2>&1 | tail -n 15
```

Peça ao fundador para recarregar o JARVIS Web e dizer se os painéis de agentes e issues do Paperclip
preencheram.

## Passo 7: celular

```sh
ipconfig getifaddr en0
ls -l ~/.jarvis/tls/jarvis-web.crt
open ~/.jarvis/tls
```

Diga ao fundador: mandar `jarvis-web.crt` por AirDrop para o iPhone, instalar o perfil e marcá-lo como
confiável em Ajustes, Geral, Sobre, Ajustes de Confiança de Certificado; depois abrir
`https://<IP do Mac>:8017/?token=<token do passo 3>`. Registre se abriu e se o microfone funcionou.

## Encerramento

Mostre o relatório completo na conversa e diga em uma linha o que ficou pendente com o fundador. Não apague
`~/DEV/install-mac.log`, `~/DEV/ffmpeg-build.log`, `~/DEV/voz.wav` nem `~/DEV/voz.txt`.
