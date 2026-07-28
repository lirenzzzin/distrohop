# Direção visual da GUI — Fase 5

## Linguagem do design

Interface original de utilitário de segurança, limpa e confiável, sem copiar
marcas ou componentes de CCleaner/Avast. Implementação somente com Tkinter,
`ttk` e `Canvas`, sem temas obtidos por `pip`.

Termos do conceito:

- **sidebar colapsável com auto-hide**;
- **stepper vertical de progresso**;
- **active pill / indicador ativo animado**;
- **cards com cantos arredondados**;
- **glassmorphism leve**, simulado por composição de cor;
- temas **light/dark**;
- transições com **ease-in-out**.

## Layout

- Janela mínima: 960 × 640; conteúdo responsivo até 1366 × 768.
- Sidebar expandida: 232 px. Recolhida: 72 px, preservando os ícones.
- A sidebar recolhe após inatividade, mas permanece aberta durante foco por
  teclado, diálogo ou operação que exija escolha.
- Cada fase possui ícone próprio, nome e estado: pendente, atual, concluída,
  aviso ou erro.
- A fase atual recebe um retângulo arredondado cinza visualmente equivalente a
  80% de opacidade. Como Tk não oferece alpha por widget, a cor será calculada
  contra o fundo pelo `Canvas`.

## Movimento

- O indicador desliza entre etapas em 220–280 ms.
- Mudanças de página usam fade/slide curto de 160–220 ms.
- A animação usa `after()` e interpolação cúbica, sem bloquear o motor.
- O estado interno muda imediatamente; a animação é apenas apresentação.
- Haverá modo de movimento reduzido, sem loops decorativos.

## Aparência

- Fonte preferida pela plataforma, com fallback seguro.
- Ícones vetoriais desenhados no `Canvas` e glifos seguros da fonte da
  plataforma, sem ativos externos ou dependências.
- Bordas discretas, contraste alto, espaçamento generoso e no máximo uma cor de
  destaque por tela.
- Tema inicial segue o sistema quando detectável; escolha do usuário prevalece.
- Alertas perigosos não dependem apenas de cor: usam ícone, título e texto.

## Fluxos

Backup:

1. detectar;
2. selecionar;
3. escolher destinos;
4. proteger;
5. copiar;
6. verificar;
7. concluir.

Restore:

1. ler bundle;
2. validar manifesto;
3. selecionar;
4. preparar a distro;
5. aplicar;
6. verificar;
7. concluir.

CLI e GUI chamarão exatamente o mesmo motor. A GUI consumirá eventos por uma
fila e atualizará widgets pela thread principal.

## Implementação da Fase 5

- `distrohop` sem argumento prefere a GUI; `--cli` e subcomandos nunca carregam
  Tk; `--gui` falha com instrução específica da distro quando o runtime falta.
- Os assistentes expõem seleção de perfis/contas/extras, múltiplos destinos,
  cifra, dry-run, leitura do manifesto e restore same/cross-engine.
- Trabalho pesado roda em thread daemon; eventos passam por `queue.Queue` e só
  a thread principal toca nos widgets.
- A sidebar anima entre 232 e 72 px e cancela animações antigas quando o fluxo
  muda, evitando indicador visual fora de sincronia com o estado do motor.
