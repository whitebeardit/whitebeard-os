# Validação no Mac zerado

Objetivo: provar que o papel **desktop** instala num macOS recém-formatado, sem nenhum dado privado, só com
o tenant de exemplo. O que falhar aqui é bug do kernel, não da máquina.

## 1. Antes de começar

- macOS atualizado, usuário comum (não root). Não instale Homebrew, Node, Python nem Docker antes: o ponto
  é começar do zero. As Command Line Tools (git, python3) o instalador pede sozinho.
- Enquanto o repositório for privado: chave SSH cadastrada no GitHub (`ssh-keygen -t ed25519` e colar a
  pública em github.com/settings/keys). Só isso.

## 2. Instalar

Um comando:

```sh
git clone git@github.com:whitebeardit/jarvis-os.git ~/DEV/jarvis-os && ~/DEV/jarvis-os/install.sh
```

Se o `git` ainda não existir, o macOS abre o diálogo das Command Line Tools na primeira chamada; confirme,
espere terminar e repita o comando. Quando o repositório for público, o mesmo passa a ser
`curl -fsSL https://raw.githubusercontent.com/whitebeardit/jarvis-os/main/install.sh | sh`.

Esperado: nenhuma pergunta interativa além do diálogo das Command Line Tools; termina com o bloco
`---- resumo ----` mostrando uv, node, claude, 1 skill instalada (`exemplo-ola`), 2 MCPs
(`exemplo_knowledge`, `exemplo_ferramentas`), 0 segredos obrigatórios faltando.

## 3. Conferir

Abra um terminal novo e rode:

```sh
echo "$JARVIS_TENANT $JARVIS_ROLE $JARVIS_HOME"
node -v
uv --version
claude --version
ls -la ~/.claude/skills/exemplo-ola
claude mcp get exemplo_knowledge
ls -l ~/.jarvis/secrets.env        # deve ser -rw------- (600)
```

Depois rode o bootstrap de novo: deve terminar igual, sem duplicar linhas no `~/.zshrc` e sem erro
(idempotência).

## 4. Opcional: Hermes

```sh
./bootstrap.sh --tenant tenants/exemplo/jarvis.yaml --with-hermes
hermes --version
```

Isso valida o instalador upstream do Hermes no macOS na tag do lockfile. Não configure Telegram: o papel
desktop não roda gateway com bot de produção.

## 5. O que reportar

- Saída completa do bootstrap (as duas execuções) e do bloco de conferência.
- `sw_vers` e `uname -m`.
- Qualquer pergunta interativa que tenha aparecido (não deveria haver nenhuma).

## 6. Próximo passo depois de verde

Trocar o tenant de exemplo pelo tenant real, que vive num repositório privado, e repetir os passos 2 e 3.
Só então entra o app JARVIS desktop.
