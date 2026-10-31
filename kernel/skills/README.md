# Skills do kernel

Só mecanismo: método, procedimento, formato. Nada que dependa de quem é o tenant.

Teste para entrar aqui: *se outra empresa instalasse o JARVIS OS, esta skill serviria do mesmo jeito?*
Se a resposta exigir trocar nome, chat, conta, pipeline ou URL, a parte que muda vai para o pacote de tenant
e só o mecanismo fica aqui.

Cada skill é uma pasta com `SKILL.md` (frontmatter `name` e `description`). O bootstrap copia para
`$JARVIS_HOME/skills/<nome>` e linka em `~/.claude/skills/<nome>`.
