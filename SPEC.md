# distrohop — migração de perfis entre distros (e Windows)

> Este arquivo é a **especificação técnica do projeto**. Cada seção descreve requisitos executáveis e documenta explicitamente os comportamentos sutis.

## Contexto

Hoje (2026-07-28) migramos NixOS→CachyOS e Brave→Zen na mão: decriptamos o keyring GNOME e 654 cookies do Brave com openssl puro, injetamos no `cookies.sqlite` do Zen, e queimamos um ciclo inteiro num bug de unidade de tempo (Zen usa milissegundos, gravamos segundos → tudo deslogado). Senhas e favoritos ainda precisam de import manual pela UI.

Nada disso deveria ser refeito na próxima troca de distro. O objetivo é uma CLI que detecta distro/browsers/contas de IA, deixa o usuário escolher o que salvar, grava num destino que sobrevive à formatação, e reaplica do outro lado — reconhecendo a distro nova e instalando o browser quando for seguro fazer isso.

Princípio que rege o design inteiro: **automático onde dá, manual onde é mais seguro.** O app nunca "tenta e torce". Quando a operação é reversível e verificável, ele executa; quando pode quebrar o boot, sobrescrever dados ou brigar com um gerenciador declarativo, ele para e explica exatamente o que você deve fazer.

### A fronteira automático / manual

| Automático (o app faz) | Manual guiado (o app explica e espera) |
|---|---|
| Detectar plataforma, distro, browsers, perfis, contas de IA, discos | Criar partição-cofre quando exige encolher fs não-btrfs |
| Copiar perfil `raw` e extrair `neutral` (cookies/senhas/favoritos) | Instalar pacote em distro **declarativa** (NixOS, Guix) |
| Decriptar cookies Chromium (keyring+AES no Linux; DPAPI no Windows) | Sobrescrever dotfile que é symlink pra `/nix/store` |
| Gravar em pasta / mídia externa / HD secundário, multi-destino | Import de senhas em browser que não tem import por arquivo |
| Cifrar/decifrar o bundle (openssl aes-256) | Reboot após instalação em distro atômica |
| Instalar browser em distro **imperativa**, flatpak, ou **winget** (Windows) | Escolher particionamento manual no instalador da distro nova |
| Injetar cookies/senhas/favoritos no perfil de destino | Confirmar que o `nixos-rebuild switch` rodou |
| Criar partição em espaço **livre** já existente (Linux) | **Aprovar a exclusão do Windows Defender** (clique + UAC) |
| Disparar o pedido de exclusão do Defender (janela + UAC) | Criar exclusão manual quando o AV **não** é o Defender |

Quando um passo cai na coluna direita, o app imprime o comando/bloco exato pra copiar, e oferece re-checar (`distrohop resume`) em vez de assumir que deu certo.

**Regra de ouro para o Defender:** o app **nunca** desativa o antivírus, **nunca** adiciona exclusão silenciosa e **nunca** exclui nada além da própria pasta. A exclusão só acontece depois de um clique explícito do usuário na janela e da aprovação do UAC. Ler cookies/senhas de browser é o mesmo comportamento de um infostealer — a heurística do Defender vai sinalizar mesmo sendo legítimo, então a solução correta é o usuário autorizar de forma transparente, não esconder.

## Não-objetivos

- Migrar extensões entre engines diferentes (Chromium→Firefox) — impossível, só se avisa.
- Migrar `localStorage`/IndexedDB cross-engine — mesma coisa; é a causa de "logou o cookie mas o site pede login".
- Sincronização contínua. Isto é um snapshot pontual, não um Syncthing.
- GUI. CLI interativa com prompts, mais flags pra rodar sem perguntas.

## Stack

Python 3.9+ **só com stdlib**, multiplataforma (Linux e Windows). Sem `pip install` — o app precisa rodar num live USB pelado ou num Windows recém-instalado. Dependências externas são só binários que já vêm no sistema:

- **Linux**: `openssl` (AES — stdlib não tem), `sqlite3` (módulo `sqlite3` da stdlib), `lsblk`/`blkid`, `libnss3` via `ctypes` (só pra senha de Firefox; degrada com aviso se faltar — confirmado em `/usr/lib/libnss3.so` nesta máquina).
- **Windows**: `ctypes` chamando `Crypt32.dll` (DPAPI) e o AES-GCM via stdlib (`hashlib`/implementação própria em `capture/aesgcm.py`, já que a stdlib não expõe AES). `tkinter` (vem embutido no instalador oficial do python.org) pra janela do Defender. `nss3.dll` via `ctypes` pra senha de Firefox. Detecção de AV e exclusão via PowerShell (`Get-MpComputerStatus`, `Add-MpPreference`). Instalação de browser via `winget` (fallback `choco` se existir).

A escolha da plataforma é a **primeira** ramificação do código (`platform.system()`), antes de qualquer detecção de distro.

## Arquitetura

Repo novo em `/home/lorenzzo/distrohop`. Módulos pequenos e testáveis isoladamente:

```
distrohop.bat / distrohop        # launchers (Windows / Unix) → bootstrap → cli
distrohop/
  bootstrap.py           # roda ANTES do app: no Windows checa Defender/exclusão
  platform_.py           # platform.system() → "linux" | "windows"; despacho
  cli.py                 # subcomandos, prompts, --dry-run global
  ui/
    defender_dialog.py   # janela tkinter do Defender (Windows-only)
  detect/
    distro.py            # /etc/os-release → id, família, gerenciador, estratégia
    windows.py           # versão do Windows, winget/choco, %LOCALAPPDATA% etc.
    browsers.py          # varre nativo + flatpak (~/.var/app) + snap + paths Win
    ai.py                # ~/.claude(+.json), ~/.codex*, ~/.gemini, ~/.kimi-code
    disks.py             # Linux: lsblk -J; marca boot/raiz. Win: só volumes p/ destino
  capture/
    profile_raw.py       # cópia fiel do diretório de perfil
    chromium_linux.py    # keyring GNOME/KWallet + AES-CBC (v10/v11)
    chromium_win.py      # DPAPI (Crypt32) + AES-GCM (v10) + nota App-Bound (Chrome 127+)
    aesgcm.py            # AES-GCM puro (stdlib não expõe) p/ cookies do Windows
    firefox.py           # key4.db/logins.json via ctypes (libnss3/nss3.dll) — cross-OS
    neutral.py           # escreve cookies.jsonl / logins.csv / bookmarks.html
    extras.py            # ssh, gpg, dotfiles (resolve symlink), lista de pacotes
  vault/
    bundle.py            # layout + manifest.json + checksums
    crypto.py            # openssl (Linux) / AES via ctypes+stdlib (Win), opt-in
    targets.py           # multi-destino, verificação pós-escrita
    partition.py         # cofre (Linux-only); pré-checks paranoicos; aborta em dúvida
  restore/
    installer.py         # despacha p/ estratégia da plataforma
    win_installer.py     # winget install <id> (fallback choco)
    apply_raw.py         # devolve perfil idêntico (mesmo browser)
    apply_neutral.py     # injeta cross-engine
    nixos.py             # gera NIXOS.md com blocos prontos
  data/
    browsers.json        # caminhos de perfil por browser × plataforma × empacotamento
    packages.json        # pacote por distro (+AUR/COPR/PPA), id flatpak, id winget/choco
```

## Formato do bundle

```
distrohop-pclox-20260728-1430/
  manifest.json        # SEMPRE em claro, mesmo com bundle cifrado
  README.txt           # "NÃO FORMATAR" + como restaurar sem o app
  browsers/<browser>/<perfil>/raw/       # cópia fiel
  browsers/<browser>/<perfil>/neutral/   # cookies.jsonl, logins.csv, bookmarks.html
  ai/<ferramenta>/<slot>/                # codex1, codex2, claude1…
  system/{ssh,gpg,dotfiles,packages}/
```

`manifest.json` guarda: versão do formato, distro de origem, browsers+versões, o que foi capturado, se está cifrado, e sha256 por arquivo. Fica fora da cifra de propósito — você precisa saber o que tem no HD sem digitar senha.

Cifra opt-in: `tar` → `openssl enc -aes-256-cbc -pbkdf2 -salt` → `bundle.tar.enc`. Sem cifra, tudo `chmod 600`/`700`. A escolha é perguntada na hora, com o aviso de que sem cifra qualquer um com o HD lê senhas e tokens de IA.

## Compatibilidade de distros

`detect/distro.py` mapeia `ID`/`ID_LIKE` para uma das quatro estratégias:

- **imperativa** — pacman, apt, dnf, zypper, apk, xbps, emerge. Instala direto.
- **atômica** — `rpm-ostree` (Silverblue/Kinoite/Bazzite), `transactional-update` (MicroOS). Instala e **exige reboot** antes de aplicar dados; o app para e retoma com `distrohop resume`.
- **declarativa** — **NixOS**, Guix. Não instala nada. Ver abaixo.
- **fallback** — flatpak (id do `data/packages.json`), ou tarball/AppImage oficial do browser.

Distro desconhecida não é erro fatal: cai no fallback e segue.

### NixOS — manual, mas amigável

Restaurar *dados* no NixOS é normal (`$HOME` é mutável). O que não dá é instalar pacote imperativamente. Então:

1. Detecta `ID=nixos` e descobre o estilo: `/etc/nixos/flake.nix`, `/etc/nixos/configuration.nix`, ou home-manager (`~/.config/home-manager/home.nix`).
2. Gera `NIXOS.md` no bundle com o bloco exato pra colar no arquivo certo — `environment.systemPackages = with pkgs; [ ... ];` ou o equivalente home-manager, mais `services.flatpak.enable = true;` se o browser só existir como flatpak.
3. Mostra `sudo nixos-rebuild switch` (ou `home-manager switch`) e **espera**. `distrohop resume` re-checa se o binário apareceu e só então aplica os perfis.
4. **Dotfiles:** antes de escrever, testa se o alvo é symlink pra `/nix/store`. Se for, não sobrescreve — grava em `<arquivo>.distrohop-restore` ao lado e mostra o valor pra colocar no nix config. Sobrescrever ali ou falha (read-only) ou é revertido no próximo rebuild; os dois casos confundem o usuário.
5. **Lista de pacotes:** traduz o que der pra nome de atributo nix; o que não achar vai numa seção "procure em search.nixos.org" em vez de chutar nome errado.

Na direção oposta (backup *feito no* NixOS), `capture/extras.py` resolve symlinks de `/nix/store` pro conteúdo real e sanitiza paths `/nix/store/...` hardcoded — foi exatamente o que quebrou o `~/.kimi-code` (binário patchelfado do NixOS, inútil no Arch).

## Windows

O app roda no Windows como plataforma de primeira classe, tanto pra backup quanto pra restore. Não é porte parcial: `distrohop list/backup/restore` funcionam igual, só mudam os módulos de baixo nível.

### Launcher e bootstrap (o que acontece "quando abre")

`distrohop.bat` é o ponto de entrada. Ele:
1. Acha o Python (`py -3` → `python` → `python3`); se nenhum, abre uma janela dizendo pra instalar (`winget install Python.Python.3.12`) e sai. Não tenta instalar sozinho.
2. Chama `python -m distrohop.bootstrap`. **Nada do app roda antes disso** — é a exigência do usuário: primeiro o portão do Defender, depois o app.

`bootstrap.py` (Windows-only; no Linux é no-op e cai direto no `cli`):
1. Descobre a pasta do app (`sys.argv[0]` resolvido) — é a **única** coisa que será excluída.
2. `Get-MpComputerStatus` via PowerShell → o Defender é o AV **ativo** e em tempo real?
3. Consulta as exclusões atuais (`Get-MpPreference | Select ExclusionPath`). Se a pasta já está excluída → segue direto pro app.
4. **Se Defender ativo e sem exclusão:** abre `defender_dialog.py` (tkinter). A janela explica, em português e sem jargão: *"Este app lê os cookies e senhas do seu navegador pra migrar seu login. Isso se parece com o que um vírus faz, então o Windows Defender pode bloquear ou apagar o app. Clique em Habilitar pra liberar só esta pasta (`<caminho>`). Vai aparecer um pedido de permissão do Windows (UAC) — é normal."* Botões: **[Habilitar exclusão]** e **[Continuar sem exclusão]**.
   - **Habilitar** → `Start-Process powershell -Verb RunAs -ArgumentList "Add-MpPreference -ExclusionPath '<pasta>'"`. Isso dispara o **UAC** (não há como adicionar exclusão sem elevação — e é proposital). Se o usuário aprovar, confirma que a exclusão entrou e segue. Se recusar/cancelar o UAC, volta pro diálogo.
   - **Continuar sem exclusão** → segue mesmo assim, avisando que o Defender pode quarentenar arquivos no meio da operação.
5. **Se o AV não é o Defender** (Norton, Kaspersky, Avast, etc., detectados via `Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntiVirusProduct`): não dá pra automatizar. A janela mostra o caminho da pasta e um passo-a-passo genérico ("adicione esta pasta como exceção/exclusão no seu antivírus"), com botão **[Já fiz, continuar]**.
6. Estado guardado num arquivo local (`.distrohop-bootstrap.json`) pra não repapaguear o diálogo a cada execução; um `--reset-bootstrap` limpa.

Limites explícitos, codificados: nunca `Set-MpPreference -DisableRealtimeMonitoring`, nunca exclusão de processo/extensão, nunca caminho fora da pasta do app, nunca rodar elevado o app inteiro — só o único comando `Add-MpPreference`. Tudo o que o app faz com o Defender é registrado no log em claro.

### Decriptação de cookies no Windows

Diferente do Linux (keyring + AES-CBC). Chromium no Windows:
- `Local State` → `os_crypt.encrypted_key`, base64-decode, tira o prefixo `DPAPI` (5 bytes) → `CryptUnprotectData` (DPAPI, via `Crypt32.dll`) devolve a chave AES de 256 bits.
- Cookie/senha com prefixo `v10`: `[3B "v10"][12B nonce][ciphertext][16B tag]` → **AES-256-GCM** (`capture/aesgcm.py`). Sem o strip-32 do Linux.
- **App-Bound Encryption (Chrome/Edge 127+):** a chave passou a ser protegida por um serviço COM ligado ao próprio browser, e o DPAPI puro não basta. O app **detecta** esse caso (chave com prefixo `APPB` em vez de `DPAPI`) e, em vez de falhar torto, avisa que aquele perfil específico exige o browser rodando/consentimento e cai no plano `neutral` só com o que der (favoritos sempre saem; cookies desse perfil podem não sair). Brave e a maioria ainda usam DPAPI clássico — funcionam direto.

Firefox/Zen no Windows usam o **mesmo** `key4.db`/`logins.json` do Linux, só trocando `libnss3.so` por `nss3.dll` — `capture/firefox.py` é cross-OS.

### Caminhos no Windows (`data/browsers.json`)

- Chromium: `%LOCALAPPDATA%\<Vendor>\<Browser>\User Data\<Profile>` (Brave: `BraveSoftware\Brave-Browser`; Chrome: `Google\Chrome`; Edge: `Microsoft\Edge`).
- Firefox/Zen: `%APPDATA%\Mozilla\Firefox` e `%APPDATA%\zen`, perfis via `profiles.ini`.
- IA e dotfiles: `%USERPROFILE%\.claude`, `.codex`, `.gemini`, etc. — mesmos nomes do Linux.

### Instalação e destino no Windows

- Instalar browser: `winget install <id>` (`Brave.Brave`, `Mozilla.Firefox`, `Zen-Team.Zen-Browser`, `Google.Chrome`), fallback `choco` se presente. É a "estratégia imperativa" do Windows.
- **Partição-cofre não existe no Windows** — sem `lsblk`/btrfs e sem o mesmo modelo de risco. No Windows os destinos são só **pasta**, **mídia externa** e **HD/volume secundário**. Mexer em `diskpart`/encolher NTFS fica fora de escopo.

## Partição-cofre — regras de aborto (Linux)

Ordem de preferência: **espaço livre existente** > encolher btrfs > recusar.

Pré-checks, todos obrigatórios, qualquer falha aborta com explicação:
1. Já existe backup íntegro (checksum conferido) em outro destino. Sem isso, nem começa.
2. O fs a encolher é btrfs (encolhe online com segurança via `btrfs filesystem resize`). ext4 montado ou xfs → **recusa** e manda usar live USB/GParted.
3. Espaço livre real ≥ tamanho pedido + 20% de margem.
4. Nenhum snapshot/balance/scrub em andamento.
5. Confirmação digitada por extenso, não `[y/N]`.

Partição criada é GPT padrão, nome `DISTROHOP-DO-NOT-FORMAT`, label igual, ext4, com o README dentro. **Não** toca em bootloader, `/etc/fstab`, nem em ordem de partições — nada que possa quebrar a instalação de outra distro.

**Portão obrigatório antes de criar a partição.** Assim que o usuário escolhe "criar partição-cofre", antes de qualquer pré-check, o app mostra em destaque:

> ⚠️ Uma partição-cofre **só sobrevive se, ao instalar a nova distro, você escolher "particionamento manual" (manual partition) e NÃO marcar esta partição pra formatar.** Se você escolher "apagar o disco inteiro" / "erase disk", o instalador destrói o cofre junto — não existe flag ou truque que impeça isso. Se não pretende usar particionamento manual, use um destino externo.

O usuário confirma digitando por extenso que entendeu, e o app **exige** que exista pelo menos um segundo destino (externo ou pasta) com backup íntegro antes de prosseguir — o cofre nunca é a única cópia. Multi-destino é o padrão recomendado justamente por isso; o modo "só a pasta" existe pra quem quer decidir depois.

## Armadilhas já conhecidas (custaram tempo hoje — codificar como teste)

- **Zen/Firefox grava tempo em milissegundos** em `expiry`, `creationTime`, `lastAccessed`, `updateTime`. Chromium usa microssegundos desde 1601. Conversão: `ms = int(chromium_utc/1e6 - 11644473600) * 1000`. Errar isso desloga tudo silenciosamente.
- **Chromium ≥130** prefixa o plaintext do cookie com 32 bytes (sha256 do domínio) → descartar `plain[:32]`.
- `v10` usa chave derivada de `"peanuts"`; `v11` usa o segredo do keyring GNOME. Um perfil tem os dois misturados.
- Matar o browser com `pkill -f zen` casa com o próprio script e ele se auto-mata. Usar `pkill -x`.
- Escrever no `cookies.sqlite` com o browser aberto perde tudo no WAL. Fechar antes, `PRAGMA wal_checkpoint(TRUNCATE)` depois.

## Testes

- **Perfis sintéticos**: fixtures que geram `cookies.sqlite`, `Login Data` e `Local State` falsos → roda captura → confere valores. Os bugs de ms e do prefixo de 32B viram testes de regressão explícitos.
- **AES-GCM (Windows)**: `aesgcm.py` validado contra vetores conhecidos (NIST) — é código cripto próprio, então tem que bater byte a byte antes de confiar.
- **Bootstrap do Defender**: a lógica de decisão (AV ativo? já excluído? Defender vs terceiro?) é testada parseando saídas de PowerShell **capturadas em fixture** (string), sem chamar PowerShell de verdade nem exigir Windows. O único ponto que toca o sistema (`Start-Process -Verb RunAs`) fica atrás de uma interface mockável.
- **`partition.py` só contra imagem em loopback** (`losetup`), nunca disco real. Cada pré-check tem um teste que prova que ele aborta.
- **Matriz de plataforma/distro**: `os-release` falsos de ~12 distros + caso Windows → confere estratégia (imperativa/atômica/declarativa/flatpak/winget) e id de pacote escolhidos.
- `--dry-run` em todo comando destrutivo, imprimindo exatamente o que seria escrito.

Nota de ambiente: o desenvolvimento é num Linux (CachyOS), então os módulos Windows são cobertos por **teste unitário com mock/fixture** — DPAPI, AES-GCM, e a lógica do bootstrap não exigem uma máquina Windows pra validar a lógica. Um smoke test real no Windows fica como passo manual do usuário.

## Fases de implementação

1. **Núcleo de detecção** — `platform_.py`, `detect/*` + `cli.py list`. Entrega: `distrohop list` mostra plataforma/distro, browsers, perfis, contas de IA, discos candidatos. Nada é escrito.
2. **Captura + bundle (Linux)** — `capture/{profile_raw,chromium_linux,firefox,neutral,extras}.py`, `vault/{bundle,crypto,targets}.py`. Entrega: `distrohop backup` gera bundle verificado em N destinos. Porta o pipeline de cookies que já funciona.
3. **Restore same-engine (Linux)** — `apply_raw.py` + `installer.py` imperativa/flatpak. Reinstala browser e devolve o perfil idêntico.
4. **Restore cross-engine** — `apply_neutral.py`, com os gotchas acima como teste.
5. **Declarativa/atômica** — `restore/nixos.py`, `resume`, dotfiles com guarda de `/nix/store`.
6. **Windows** — `bootstrap.py`, `ui/defender_dialog.py`, `detect/windows.py`, `capture/{chromium_win,aesgcm}.py`, `restore/win_installer.py`, `distrohop.bat`. Entrega: o ciclo backup/restore rodando no Windows com o portão do Defender. Reaproveita bundle/neutral/firefox das fases anteriores.
7. **Partição-cofre** — `vault/partition.py`. Último de propósito: é a parte perigosa e a menos essencial.

Fases 1–4 já resolvem a próxima troca de distro. 5–7 são acabamento (Windows e cofre).

## Verificação end-to-end

- `distrohop list` nesta máquina deve achar: CachyOS/pacman/imperativa, Brave+Chrome+Chromium+Zen com seus perfis, `.claude`/`.codex`/`.codex-conta2`/`.gemini`/`.kimi-code`, e os discos `BACKUP` (931G) e `VIAGEM` (58G) como destinos válidos, com `sdb` marcado como disco de sistema.
- `distrohop backup --dry-run` lista arquivo por arquivo sem escrever nada.
- Backup real do Zen atual pra `/run/media/lorenzzo/BACKUP`, depois restore num perfil Zen limpo descartável — os 654 cookies devem voltar com validade futura e os sites logados. Esse é o teste que prova o ciclo.
- Restore cross-engine Brave→Zen num perfil de teste, comparando contagem de cookies e ausência de `expiry` em 1970.
- Suíte de testes verde antes de cada fase ser dada como pronta.

## Riscos aceitos

- Cofre no disco raiz não sobrevive a "apagar disco inteiro" — mitigado por multi-destino, portão de confirmação e exigência de segundo destino íntegro.
- Senha de Firefox precisa de `libnss3`/`nss3.dll` compatível; se faltar, senhas são puladas com aviso (o resto continua).
- Nome de pacote varia entre distros e muda com o tempo; `data/packages.json` erra às vezes → fallback flatpak/winget e mensagem clara em vez de falha silenciosa.
- Sessões guardadas em `localStorage` não migram cross-engine. O app avisa quais sites tendem a exigir re-login.
- **Windows:** perfis de Chrome/Edge 127+ com App-Bound Encryption podem não exportar cookies (favoritos sempre saem); Brave e a maioria funcionam. O app detecta e avisa em vez de falhar torto.
- **Defender:** se o usuário recusar a exclusão e o AV for terceiro, arquivos podem ser quarentenados no meio da operação. O app avisa e continua; o usuário pode religar a exclusão e rodar `distrohop resume`.
