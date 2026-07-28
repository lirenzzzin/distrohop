# distrohop

Migração segura de perfis de navegador e credenciais de ferramentas de IA
entre distribuições Linux. Windows será adicionado numa fase posterior.

O projeto está sendo implementado na ordem definida em `SPEC.md`. As Fases 1 a
4 fornecem inventário, backup e restore no Linux: cópia raw do perfil,
cookies/senhas/favoritos neutros, contas de IA, SSH/GPG/dotfiles, inventário de
pacotes, cifra opcional, gravação verificada em múltiplos destinos e restauração
atômica same-engine ou conversão Chromium↔Firefox, sempre com cópia de segurança
obrigatória do perfil anterior.

Requisitos atuais:

- Python 3.9 ou superior;
- somente biblioteca padrão;
- `lsblk` no Linux para inventário de discos.
- `openssl` para cifra do bundle e para decriptar dados Chromium;
- `libnss3` para senhas Firefox/Zen (sem ela, o restante continua com aviso);
- `secret-tool` ou `kwallet-query` para dados Chromium `v11` (sem acesso ao
  keyring, a cópia raw continua e as credenciais afetadas são avisadas).

Uso a partir do código-fonte:

```sh
./bin/distrohop list
./bin/distrohop list --json
./bin/distrohop backup --dry-run
./bin/distrohop backup --target /run/media/usuario/BACKUP
./bin/distrohop backup --target /mnt/hd1 --target /mnt/hd2 --encrypt
./bin/distrohop restore /mnt/hd1/distrohop-meu-pc-20260728-1200 --dry-run
./bin/distrohop restore /mnt/hd1/distrohop-meu-pc-20260728-1200 --browser firefox
./bin/distrohop restore /mnt/hd1/distrohop-meu-pc-20260728-1200 \
  --browser brave --target-browser zen --dry-run
python3 -m distrohop list
```

`backup --dry-run` não cria pastas nem arquivos e enumera cada origem e saída.
Backup real nunca sobrescreve um bundle existente. Sem `--encrypt`, todo o
conteúdo fica legível para quem acessar o disco e recebe permissões `600/700`.
Com `--encrypt`, a senha é solicitada sem eco ou lida por `--password-file`;
ela nunca é aceita como argumento de linha de comando. `manifest.json` continua
em claro por projeto.

`restore --dry-run` enumera arquivo por arquivo sem alterar o perfil. O restore
recusa continuar enquanto o navegador estiver aberto, verifica todos os
checksums e troca o perfil de forma atômica. O perfil anterior fica ao lado do
novo com sufixo `.distrohop-before-<data>`. Se o navegador estiver ausente,
`--install` usa receita nativa verificada pela distribuição e cai para Flatpak
quando necessário.

Ao trocar de engine com `--target-browser`, cookies e favoritos são convertidos
para o banco nativo do destino. A conversão preserva os epochs distintos de
Chromium/Firefox e o prefixo criptográfico exigido pelo Chromium moderno.
Senhas ficam em `distrohop-logins.csv` para importação manual, pois os cofres
NSS e Chromium não são intercambiáveis; sessões baseadas em `localStorage`
podem exigir novo login.

Compatibilidade detalhada: [docs/LINUX-COMPATIBILITY.md](docs/LINUX-COMPATIBILITY.md).
Direção visual da GUI: [docs/GUI-DESIGN.md](docs/GUI-DESIGN.md).
