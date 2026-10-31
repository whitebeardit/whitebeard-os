# Tenants

Aqui fica só o tenant **de exemplo**, fictício. Ele valida o instalador no CI e é o ponto de partida de quem
começa do zero: copie `exemplo/` para um repositório privado seu, troque os valores e aponte o bootstrap para
o seu `jarvis.yaml`.

Tenants reais nunca entram neste repositório. Um pacote de tenant real contém:

- `jarvis.yaml` (identidade, MCPs, perfis, crons, nós; segredos só como `${NOME}`)
- `skills/` de negócio
- `denylist.txt` com os termos que não podem aparecer no kernel (nomes de clientes, contas, hosts), usado
  pelo CI do próprio tenant com `kernel/bin/privacy_lint.py --denylist`
- nunca `secrets.env`: ele é gerado por máquina e fica em `$JARVIS_HOME` com modo 600
