# distrohop

Migração segura de perfis de navegador e credenciais de ferramentas de IA
entre distribuições Linux. Windows será adicionado numa fase posterior.

O projeto está sendo implementado na ordem definida em `SPEC.md`. As Fases 1 e
2 fornecem inventário somente leitura e backup Linux: cópia raw do perfil,
cookies/senhas/favoritos neutros, contas de IA, SSH/GPG/dotfiles, inventário de
pacotes, cifra opcional e gravação verificada em múltiplos destinos.

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
python3 -m distrohop list
```

`backup --dry-run` não cria pastas nem arquivos e enumera cada origem e saída.
Backup real nunca sobrescreve um bundle existente. Sem `--encrypt`, todo o
conteúdo fica legível para quem acessar o disco e recebe permissões `600/700`.
Com `--encrypt`, a senha é solicitada sem eco ou lida por `--password-file`;
ela nunca é aceita como argumento de linha de comando. `manifest.json` continua
em claro por projeto.

Compatibilidade detalhada: [docs/LINUX-COMPATIBILITY.md](docs/LINUX-COMPATIBILITY.md).
Direção visual da GUI: [docs/GUI-DESIGN.md](docs/GUI-DESIGN.md).
