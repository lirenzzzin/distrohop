# Headless Linux VM lab

[English](#english) · [Português](#português)

## English

The Distrohop VM lab validates the same committed source revision on multiple
Linux families without opening a window or taking over the desktop. It runs
QEMU directly, without a libvirt daemon, and stores all large files outside the
repository.

### Safety and resource contract

- one VM at a time;
- exactly 2 vCPUs and at most **2048 MiB RAM** per guest;
- start is refused unless the host also has a 1024 MiB memory margin;
- QEMU runs under `nice -n 15` and idle-class I/O scheduling;
- no graphical QEMU display, audio, USB passthrough, bridge, or physical disk;
- SSH is key-only and forwarded to `127.0.0.1`, never to the LAN;
- every guest uses an immutable verified base plus a disposable qcow2 overlay;
- partition tests can only touch the guest's dedicated 8 GiB `/dev/vdb`;
- downloads are resumable, checksum-verified, and limited to 4 MiB/s; and
- no image download or VM start occurs merely by running `doctor` or `list`.

The default state directory is
`~/.local/share/distrohop/vm-lab`. Set `DISTROHOP_VM_HOME` to use another
location. This directory contains cloud images, a lab-only SSH key, overlays,
logs, and JSON reports. It must not be committed.

### Host setup

The host needs KVM, QEMU x86_64, `qemu-img`, OVMF, `cloud-localds`, OpenSSH,
Git, curl, `nice`, and `ionice`. On Arch Linux and derivatives:

```bash
sudo pacman -S --needed qemu-base cloud-image-utils edk2-ovmf
python3 tools/vm_lab.py doctor
```

`doctor` is the authoritative check because package names vary between host
distributions. It prints every missing executable, KVM permission, firmware
status, and whether the current free-memory gate would allow a guest.

### Matrix

| Target | Native setup | Tier | Status |
| --- | --- | --- | --- |
| Ubuntu 26.04 LTS | APT | core | automated |
| Debian 13 | APT | core | automated |
| Fedora 44 | DNF | core | automated |
| Arch Linux | pacman | core | automated |
| openSUSE Tumbleweed | Zypper | core | automated |
| Alpine 3.24 | APK | core | automated |
| openSUSE MicroOS | transactional-update | extended | manual, reboot-aware harness pending |
| NixOS | Nix | extended | manual, reproducible cloud-image generation pending |

Core entries use official HTTPS cloud images and official checksum files.
Rolling images are verified on every fetch. Matrix definitions and
distro-specific package commands live in
[`tools/vm/matrix.json`](../tools/vm/matrix.json).

MicroOS is not silently treated like ordinary Tumbleweed: package installation
is transactional and needs a reboot/resume-aware two-boot cycle. NixOS is also
not disguised as an imperative distro: the official release publishes an
installer ISO, so automated testing needs a separately generated,
reproducible cloud image. Both remain visible as extended targets until those
flows are implemented.

### Run one test cycle

The commands are intentionally separate, so downloading or allocating RAM is
always explicit:

```bash
# Read-only status
python3 tools/vm_lab.py list
python3 tools/vm_lab.py doctor

# One-time per distro: bandwidth-limited download and disposable instance
python3 tools/vm_lab.py fetch debian-13
python3 tools/vm_lab.py create debian-13

# Runtime cycle
python3 tools/vm_lab.py start debian-13
python3 tools/vm_lab.py wait debian-13
python3 tools/vm_lab.py setup debian-13
python3 tools/vm_lab.py sync debian-13
python3 tools/vm_lab.py test debian-13
python3 tools/vm_lab.py stop debian-13

# Optional: discard only this stopped overlay and virtual data disk
python3 tools/vm_lab.py destroy debian-13 --yes
```

`sync` sends only the committed `HEAD` archive, not the working tree, Git
credentials, browser profiles, cookies, or host configuration. Commit the
revision you want to validate before syncing it.

To test another core target, repeat the cycle with its ID from `list`. Never
start two targets in parallel: the launcher enforces the one-VM policy.

### What runs inside the guest

The guest runner:

1. executes the complete Python unit suite;
2. runs `distrohop list --json` and checks the expected distro/package manager;
3. creates a synthetic, non-secret AI-account fixture;
4. checks the backup dry-run;
5. validates that `/dev/vdb` is the exact dedicated 8 GiB lab disk;
6. formats and mounts only that virtual disk;
7. creates and verifies a real bundle; and
8. starts the Tk GUI under Xvfb at 1280×800, waits for detection, toggles
   light/dark and Portuguese/English, and opens the backup selection screen.

The resulting report is copied to
`~/.local/share/distrohop/vm-lab/reports/<distro>.json`. Serial boot logs and
QEMU logs remain beside the instance for diagnosis.

This lab exercises package families and headless GUI behavior under a strict
2 GiB ceiling. It does not replace a final test on real hardware, an
accessibility review, Windows validation, or compositor-specific visual
inspection.

Useful upstream references:

- [QEMU documentation](https://www.qemu.org/docs/master/)
- [cloud-init distribution support](https://cloudinit.readthedocs.io/en/latest/reference/distros.html)
- [Ubuntu cloud images](https://cloud-images.ubuntu.com/)
- [Debian cloud images](https://cloud.debian.org/images/cloud/)
- [Fedora Cloud](https://fedoraproject.org/cloud/download/)
- [Arch Linux cloud images](https://wiki.archlinux.org/title/Arch_Linux_on_a_VPS)
- [openSUSE appliances](https://download.opensuse.org/tumbleweed/appliances/)
- [Alpine cloud images](https://dl-cdn.alpinelinux.org/alpine/latest-stable/releases/cloud/)

## Português

O laboratório de VMs do Distrohop valida a mesma revisão commitada do código
em várias famílias Linux sem abrir janela nem tomar o foco do desktop. Ele
executa o QEMU diretamente, sem daemon do libvirt, e guarda todos os arquivos
grandes fora do repositório.

### Contrato de segurança e recursos

- uma VM por vez;
- exatamente 2 vCPUs e no máximo **2048 MiB de RAM** por guest;
- o início é recusado sem uma margem adicional de 1024 MiB no host;
- o QEMU roda com `nice -n 15` e I/O na classe idle;
- não há display gráfico do QEMU, áudio, USB passthrough, bridge ou disco físico;
- o SSH usa somente chave e encaminha para `127.0.0.1`, nunca para a rede local;
- cada guest usa uma base imutável verificada e um overlay qcow2 descartável;
- testes de partição só podem tocar o `/dev/vdb` virtual dedicado de 8 GiB;
- downloads retomam de onde pararam, verificam checksum e limitam-se a 4 MiB/s; e
- `doctor` e `list` nunca baixam imagens nem iniciam uma VM.

O estado fica por padrão em `~/.local/share/distrohop/vm-lab`. Defina
`DISTROHOP_VM_HOME` para usar outro local. Essa pasta contém imagens cloud, uma
chave SSH exclusiva do laboratório, overlays, logs e relatórios JSON. Ela não
deve ser commitada.

### Preparação do host

O host precisa de KVM, QEMU x86_64, `qemu-img`, OVMF, `cloud-localds`, OpenSSH,
Git, curl, `nice` e `ionice`. No Arch Linux e derivados:

```bash
sudo pacman -S --needed qemu-base cloud-image-utils edk2-ovmf
python3 tools/vm_lab.py doctor
```

O `doctor` é a conferência definitiva porque nomes de pacotes variam entre
distros do host. Ele mostra executáveis ausentes, permissão do KVM, firmware e
se a memória livre atual passa pela trava para iniciar um guest.

### Matriz

| Alvo | Preparação nativa | Nível | Estado |
| --- | --- | --- | --- |
| Ubuntu 26.04 LTS | APT | core | automatizado |
| Debian 13 | APT | core | automatizado |
| Fedora 44 | DNF | core | automatizado |
| Arch Linux | pacman | core | automatizado |
| openSUSE Tumbleweed | Zypper | core | automatizado |
| Alpine 3.24 | APK | core | automatizado |
| openSUSE MicroOS | transactional-update | extended | manual, aguardando fluxo com reboot |
| NixOS | Nix | extended | manual, aguardando geração reproduzível de imagem cloud |

Os alvos core usam imagens cloud HTTPS oficiais e arquivos oficiais de
checksum. Imagens rolling são verificadas em todo download. A matriz e os
comandos diferentes para cada família ficam em
[`tools/vm/matrix.json`](../tools/vm/matrix.json).

O MicroOS não é tratado silenciosamente como um Tumbleweed tradicional: a
instalação é transacional e exige um ciclo de dois boots consciente da
retomada. O NixOS também não é disfarçado de distro imperativa: a release
oficial fornece uma ISO de instalação, então a automação precisa antes gerar
uma imagem cloud reproduzível. Ambos continuam visíveis como alvos extended
até esses fluxos existirem.

### Execute um ciclo

Os comandos são separados de propósito, para download e alocação de RAM serem
sempre explícitos:

```bash
# Estado somente leitura
python3 tools/vm_lab.py list
python3 tools/vm_lab.py doctor

# Uma vez por distro: download limitado e instância descartável
python3 tools/vm_lab.py fetch debian-13
python3 tools/vm_lab.py create debian-13

# Ciclo de execução
python3 tools/vm_lab.py start debian-13
python3 tools/vm_lab.py wait debian-13
python3 tools/vm_lab.py setup debian-13
python3 tools/vm_lab.py sync debian-13
python3 tools/vm_lab.py test debian-13
python3 tools/vm_lab.py stop debian-13

# Opcional: descarte somente o overlay e o disco virtual deste guest parado
python3 tools/vm_lab.py destroy debian-13 --yes
```

O `sync` envia somente o arquivo da revisão commitada em `HEAD`, não a árvore
de trabalho, credenciais Git, perfis de navegador, cookies ou configurações do
host. Faça commit da revisão que deseja validar antes da sincronização.

Para outra distro core, repita o ciclo com o ID mostrado por `list`. Nunca
inicie dois alvos em paralelo: o launcher impõe a política de uma VM.

### O que é testado dentro do guest

O runner:

1. executa toda a suíte unitária Python;
2. roda `distrohop list --json` e confere distro e gerenciador esperados;
3. cria uma fixture sintética de conta de IA, sem segredo real;
4. confere o dry-run do backup;
5. valida que `/dev/vdb` é exatamente o disco virtual dedicado de 8 GiB;
6. formata e monta somente esse disco virtual;
7. cria e verifica um bundle real; e
8. abre a GUI Tk sob Xvfb em 1280×800, aguarda a detecção, alterna
   claro/escuro e português/inglês e abre a seleção de backup.

O relatório é copiado para
`~/.local/share/distrohop/vm-lab/reports/<distro>.json`. Logs seriais do boot e
do QEMU ficam junto da instância para diagnóstico.

O laboratório cobre famílias de pacotes e a GUI headless dentro do teto de
2 GiB. Ele não substitui o teste final em hardware real, revisão de
acessibilidade, validação do Windows ou inspeção visual específica de cada
compositor.
