# Compatibilidade Linux

O distrohop é um único aplicativo, mas resolve um perfil de plataforma antes
de executar qualquer comportamento específico. `ID` é a identidade principal,
`ID_LIKE` é fallback para derivados desconhecidos e `VARIANT_ID` separa uma
edição tradicional de uma edição atômica.

Uma distro desconhecida não é erro fatal. Se houver um gerenciador conhecido,
ele pode ser identificado por capacidade; sem isso, o app usa o plano genérico
com Flatpak ou instrução manual, sem inventar comandos.

## Matriz de comportamento

| Perfil | Exemplos reconhecidos | Gerenciador do sistema | Estratégia |
|---|---|---|---|
| Arch | Arch, CachyOS, Manjaro, EndeavourOS, Garuda, Artix, KaOS | pacman | imperativa |
| Debian | Debian, Ubuntu, Mint, Pop!_OS, elementary, Zorin, Kali, MX, Deepin | apt/apt-get | imperativa |
| Fedora/RHEL | Fedora, RHEL, CentOS, Rocky, Alma, Oracle, Nobara | dnf/dnf5 | imperativa |
| SUSE | openSUSE Leap/Tumbleweed/Slowroll, SLES, SLED | zypper | imperativa |
| Alpine | Alpine, postmarketOS | apk | imperativa |
| Void | Void | XBPS | imperativa |
| Gentoo | Gentoo, Calculate | Portage/emerge | imperativa |
| Solus | Solus | eopkg | imperativa |
| Clear Linux | Clear Linux | swupd bundles | imperativa |
| Slackware | Slackware, Salix | slackpkg | imperativa com cautela |
| Mageia | Mageia | urpmi | imperativa |
| PCLinuxOS | PCLinuxOS | APT-RPM | imperativa |
| Fedora Atomic | Silverblue, Kinoite, Bazzite, Bluefin, Aurora, CoreOS | rpm-ostree | atômica, exige retomada |
| openSUSE transacional | MicroOS, Aeon, Kalpa | transactional-update | atômica, exige retomada |
| Vanilla OS | Vanilla OS | ABRoot; Flatpak para apps | atômica |
| SteamOS | SteamOS | Flatpak para apps | atômica; nunca usa pacman automaticamente |
| Endless OS | Endless OS | Flatpak para apps | atômica |
| NixOS | NixOS | Nix | declarativa, nunca instala imperativamente |
| GNU Guix | Guix System | Guix | declarativa, nunca instala imperativamente |
| blendOS | blendOS | system.yaml/Akshara; Flatpak para apps | declarativa |

Os comandos são armazenados como vetores de argumentos, não strings de shell.
Isso permite mostrar comandos exatos em `--dry-run` sem problemas de quoting e
reutilizá-los pelos instaladores das fases posteriores.

## Detecção de perfis

- Chromium usa `XDG_CONFIG_HOME` e, para Chrome/Chromium,
  `CHROME_CONFIG_HOME`. `CHROME_USER_DATA_DIR` é incluído quando estiver ativo.
- O arquivo `Local State` fornece os nomes amigáveis dos perfis Chromium.
- Firefox e derivados são resolvidos por `profiles.ini`, inclusive perfis
  relativos e absolutos.
- São varridos formatos nativo, Flatpak e Snap.
- Os caminhos ficam em `distrohop/data/browsers.json`, separados da lógica.

## Discos

`lsblk` é chamado com colunas explícitas e JSON. Primeiro se tenta
`MOUNTPOINTS`; versões antigas recebem fallback automático para `MOUNTPOINT`.
O disco físico que contém `/` e suas partições são marcados como sistema e
nunca aparecem como destinos válidos.

## Captura Linux e portabilidade

- Cada perfil recebe uma cópia `raw/` autocontida. Symlinks são resolvidos,
  ciclos são interrompidos e bancos SQLite são refeitos pela API de backup para
  incorporar WAL sem pedir que o navegador seja encerrado.
- Chromium gera `cookies.jsonl`, `logins.csv` e `bookmarks.html`. Valores `v10`
  usam a chave Linux histórica; `v11` consulta Secret Service ou KWallet. O
  hash de domínio introduzido nos cookies recentes só é removido quando os 32
  bytes conferem exatamente, evitando corromper cookies antigos.
- Firefox e Zen usam os mesmos formatos neutros. Cookies/favoritos vêm de
  snapshots SQLite; credenciais passam por `libnss3` sobre a cópia raw do
  perfil. Ausência da biblioteca ou senha primária bloqueada pula somente as
  senhas e produz aviso.
- O inventário de pacotes é específico para cada família (`pacman`, `dpkg`,
  `dnf5`/RPM, Zypper/RPM, APK, XBPS, Portage, eopkg, swupd, Nix, Guix e as
  variantes atômicas), além de Flatpak e Snap quando presentes.
- A publicação usa uma pasta temporária dentro de cada destino, releitura
  SHA-256 de todos os arquivos e rename atômico. Um nome existente nunca é
  sobrescrito.
- A captura e a montagem usam o destino selecionado com mais espaço livre como
  área de trabalho; dados grandes nunca passam pelo `tmpfs` de `/tmp`. Antes da
  primeira cópia, o app estima o pico de uso com margem e confere todos os
  destinos. O primeiro bundle verificado é promovido por rename no mesmo
  filesystem, sem duplicar toda a árvore.
- Snapshots SQLite têm espera limitada. Se um navegador mantiver o banco
  ocupado ou mudando continuamente, o backup aborta com orientação para fechar
  o navegador em vez de congelar ou publicar uma cópia inconsistente.

O bundle aberto usa permissões `700` para diretórios e `600` para arquivos. A
cifra opcional é `openssl enc -aes-256-cbc -pbkdf2 -salt`; a senha segue por
`stdin`, não aparece em argumentos de processo. Mesmo cifrado,
`manifest.json` e seus checksums permanecem legíveis.

## Restore declarativo e atômico

- NixOS gera `NIXOS.md` para `flake.nix`, `configuration.nix` ou Home Manager.
  Só atributos conhecidos entram no bloco; os demais são enviados para busca
  manual em vez de receber nome inventado.
- Guix e outros sistemas declarativos recebem `DECLARATIVE.md`; nenhuma
  instalação é executada pelo app.
- rpm-ostree e transactional-update usam o pacote nativo da família, registram
  o boot atual e recusam `resume` até detectar um boot diferente.
- `.distrohop-resume.json` não guarda senha. Ele contém apenas a seleção,
  destino e etapa pendente, com permissão `600`.
- Dotfiles gerenciados por symlink para `/nix/store` nunca são substituídos. A
  cópia fica ao lado com sufixo `.distrohop-restore`.

## Limite honesto

“Qualquer distro” significa comportamento excelente para as famílias acima e
fallback seguro para as demais. Não significa executar cegamente um comando
imperativo em sistemas novos, imutáveis ou declarativos. Quando a distro muda
seu contrato, o perfil correspondente deve ser atualizado sem alterar o motor.

## Fontes primárias

- [systemd `os-release`](https://www.freedesktop.org/software/systemd/man/latest/os-release.html):
  `ID`, `ID_LIKE` e `VARIANT_ID`
- [pacman/Arch](https://wiki.archlinux.org/title/Pacman)
- [APT/Debian](https://www.debian.org/doc/manuals/debian-reference/ch02.en)
- [Fedora Atomic](https://fedoraproject.org/atomic-desktops/) e
  [rpm-ostree](https://docs.fedoraproject.org/en-US/fedora-silverblue/getting-started/)
- [zypper/openSUSE](https://doc.opensuse.org/documentation/tumbleweed/zypper/)
- [apk/Alpine](https://docs.alpinelinux.org/user-handbook/0.1a/Working/apk.html)
- [XBPS/Void](https://docs.voidlinux.org/xbps/index.html)
- [Portage/Gentoo](https://wiki.gentoo.org/wiki/Portage)
- [NixOS](https://nixos.org/manual/nixos/stable/#sec-package-management)
- [manual do rpm-ostree](https://coreos.github.io/rpm-ostree/administrator-handbook/)
- [transactional-update SUSE](https://documentation.suse.com/smart/systems-management/html/transactional-updates/index.html)
- [GNU Guix](https://guix.gnu.org/manual/en/guix.html)
- [SteamOS](https://help.steampowered.com/pt-br/faqs/view/671A-4453-E8D2-323C),
  [Vanilla OS/ABRoot](https://abroot.vanillaos.org/) e
  [blendOS](https://blendos.co/install/post-install/intro/)
- [diretórios de usuário do Chromium](https://chromium.googlesource.com/chromium/src.git/+/HEAD/docs/user_data_dir.md)
- [armazenamento de senhas do Chromium no Linux](https://chromium.googlesource.com/chromium/src/+/ef94f6e6f1257a31fc4b8a97f21779ef249481b5/docs/linux/password_storage.md)
- [implementação OSCrypt do Chromium](https://chromium.googlesource.com/chromium/src/+/refs/tags/127.0.6533.27/components/os_crypt/sync/)
- [NSS e `key4.db` no Firefox](https://firefox-source-docs.mozilla.org/rust-components/api/js/logins.html)
- [manual `openssl enc`](https://docs.openssl.org/master/man1/openssl-enc/)
- [manual `lsblk` do util-linux](https://man7.org/linux/man-pages/man8/lsblk.8.html)
