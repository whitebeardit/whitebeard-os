# Kit de divulgação

Tudo o que aparece no README, em alta resolução, para posts, apresentações e páginas:
**[baixar o kit (zip, 58 MB)](https://github.com/whitebeardit/whitebeard-os-releases/releases/download/kit-divulgacao-2026-11/whitebeard-os-kit-divulgacao.zip)**.

Dentro do zip: `prints/` (24 PNG, a maioria em 1440×900), `videos/` (8 MP4, para Reels, Shorts e LinkedIn) e `gifs/` (os mesmos
desta pasta).

## O caso que as telas contam

O **@Pichi** é o monitor de um curso de programação no Discord e já está no ar num Raspberry Pi. O plano do curso:
tirar dúvidas dos módulos, corrigir os exercícios e, toda segunda-feira, publicar no canal a prestação de contas de
cada aluno. As telas mostram essa configuração com **dados de exemplo** (alunos, notas e entregas fictícios, bots do
Telegram com nomes `exemplo_*`). As conversas no Telegram (`telegram-pichi`) e no Discord (`discord-pichi`) são reais.

## As peças

| Peça | O que mostra | Prints no zip |
| --- | --- | --- |
| [`visao-geral.gif`](visao-geral.gif) | A visão geral; a pessoa pergunta "Quem está atrasado nas entregas?" e o JARVIS responde a partir do second brain | `visao-geral`, `visao-geral-resposta` |
| [`second-brain.gif`](second-brain.gif) | O grafo do second brain do curso, o filtro por área, a busca por "git", a nota do Módulo 3 e a árvore de mapas | `second-brain-grafo`, `-busca`, `-nota`, `-arvore` |
| [`criar-assistente.gif`](criar-assistente.gif) | Um bot novo no Discord: tipo, canal, nome, token e três pessoas reconhecidas na mesma espera | `criar-assistente-form`, `-reconhecidos` |
| [`modalidades.gif`](modalidades.gif) | Quatro bots de tipos e canais diferentes (Discord, WhatsApp, Telegram e só rotinas), o que cada um faz e não faz, e a assinatura e o nível por bot | `modalidades-topo`, `-politica`, `-whatsapp`, `-cliente`, `-assinatura` |
| [`escritorio.gif`](escritorio.gif) | O Escritório: aprovar a publicação das notas, o organograma, os cargos, as rotinas do curso e o uso de tokens | `escritorio`, `-aprovado`, `-organograma`, `-rotinas`, `-tokens` |
| [`telegram-pichi.gif`](telegram-pichi.gif) | Conversa real com o mesmo Pichi no Telegram: o que é um pull request | `telegram-pichi` |
| [`discord-pichi.gif`](discord-pichi.gif) | Conversa real com o @Pichi no Discord: a diferença entre `let` e `const`, com exemplos | `discord-pichi` |
| [`temas.gif`](temas.gif) | CONFIGURAÇÕES: os temas Atlas estelar, Carmesim e HUD, e o resultado na visão geral | `temas-configuracoes`, `-atlas`, `-carmesim`, `-hud` |

## Como foi gravado

As telas são o JARVIS Web de verdade, na versão do `main`, rodando no modo de demonstração com um conjunto de dados de
exemplo, gravadas pelo Playwright em 1440×900. Os GIFs têm 800 px de largura, 8 quadros por segundo e velocidade de
1,6×, para caber no README.
