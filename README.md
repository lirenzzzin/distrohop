# distrohop

Migração segura de perfis de navegador e credenciais de ferramentas de IA
entre distribuições Linux e Windows.

As oito fases fornecem inventário, backup e restore por CLI e GUI: cópia raw do perfil,
cookies/senhas/favoritos neutros, contas de IA, SSH/GPG/dotfiles, inventário de
pacotes, cifra opcional, gravação verificada em múltiplos destinos e restauração
atômica same-engine ou conversão Chromium↔Firefox, sempre com cópia de segurança
obrigatória do perfil anterior.

Requisitos atuais:

- Python 3.9 ou superior;
- somente biblioteca padrão;
- Linux: `lsblk` para inventário de discos e `openssl` para cifra do bundle e
  dados Chromium;
- Windows: Python oficial com Tk, PowerShell, DPAPI/Crypt32 e WinGet (Chocolatey
  é fallback);
- `libnss3` para senhas Firefox/Zen (sem ela, o restante continua com aviso);
- `secret-tool` ou `kwallet-query` para dados Chromium `v11` (sem acesso ao
  keyring, a cópia raw continua e as credenciais afetadas são avisadas).

Uso a partir do código-fonte:

```sh
git clone https://github.com/lirenzzzin/distrohop.git
cd distrohop
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

Não há dependência Python externa. Quem preferir um comando instalado pode usar
`python -m pip install --no-deps .`; isso só empacota o mesmo código e os JSONs
de dados. Em live USB ou ambiente minimalista, o launcher acima funciona sem
instalação.

No Windows, extraia a pasta e abra `distrohop.bat`. O launcher encontra Python
3.9+, executa primeiro o portão transparente do antivírus e abre a GUI. Use
`distrohop.bat --cli list` para o modo texto. O app nunca desativa o Defender:
só pode pedir, após consentimento explícito e UAC, a exclusão exata da própria
pasta.

`backup --dry-run` não cria pastas nem arquivos e enumera cada origem e saída.
Backup real nunca sobrescreve um bundle existente. Sem `--encrypt`, todo o
conteúdo fica legível para quem acessar o disco e recebe permissões `600/700`.
Com `--encrypt`, a senha é solicitada sem eco ou lida por `--password-file`;
ela nunca é aceita como argumento de linha de comando. `manifest.json` continua
em claro por projeto. No Linux, a cifra usa OpenSSL AES-256-CBC/PBKDF2; no
Windows, um contêiner AES-256-GCM/PBKDF2 autenticado e em blocos.

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

Sem argumentos, `./bin/distrohop` abre a GUI quando Tk está disponível. Use
`--cli` ou qualquer subcomando para o modo texto e `--gui` para exigir a
interface gráfica. Se Tk faltar, o launcher mostra o comando de instalação
adequado à distribuição e cai para a CLI. A GUI oferece os mesmos planos e
travas do motor, com temas claro/escuro, sidebar auto-retrátil e progresso
animado sem bloquear a janela.

Em NixOS/Guix/blendOS, o app gera orientação declarativa em vez de instalar
imperativamente. Em rpm-ostree e transactional-update, a instalação cria um
estado claro de retomada e exige um boot novo antes de aplicar dados.
`distrohop resume <bundle>` revalida ambiente e checksums antes de continuar.
Dotfiles apontando para `/nix/store` são desviados para
`.distrohop-restore`, e caminhos de binários Nix no backup são sanitizados sem
alterar o arquivo original.

No Linux, `distrohop vault create` planeja a partição-cofre em modo dry-run por
padrão. A execução exige `--execute`, root, confirmação digitada por extenso e
uma segunda cópia íntegra em outro disco. O motor prefere espaço GPT livre; só
encolhe a última partição quando ela é Btrfs single-device, tem 20% de margem e
não há balance, scrub ou snapshot/send em andamento. Ext4/XFS são recusados com
orientação para live USB/GParted. O cofre nunca altera bootloader, `fstab` ou a
ordem das partições.

Compatibilidade detalhada: [docs/LINUX-COMPATIBILITY.md](docs/LINUX-COMPATIBILITY.md).
Comportamento no Windows: [docs/WINDOWS.md](docs/WINDOWS.md).
Partição-cofre e suas travas: [docs/VAULT.md](docs/VAULT.md).
Direção visual da GUI: [docs/GUI-DESIGN.md](docs/GUI-DESIGN.md).
