"""Portuguese and English strings shared by the Tk frontends."""

from __future__ import annotations

import locale
import os
import re
import string
from typing import Mapping, Optional, Sequence, Tuple


PT_EN = {
    "Detectar": "Detect",
    "Selecionar": "Select",
    "Destino": "Destination",
    "Proteger": "Protect",
    "Copiar": "Copy",
    "Verificar": "Verify",
    "Concluir": "Finish",
    "Ler bundle": "Read bundle",
    "Validar": "Validate",
    "Preparar": "Prepare",
    "Aplicar": "Apply",
    "Tema": "Theme",
    "Recolher menu": "Collapse menu",
    "Preparando detecção…": "Preparing detection…",
    "Detectando sistema, navegadores e destinos…": (
        "Detecting system, browsers, and destinations…"
    ),
    "{} · {} · pronto": "{} · {} · ready",
    "Sistema detectado": "System detected",
    "Esta operação já está em andamento.": "This operation is already running.",
    "Operação interrompida com segurança": "Operation stopped safely",
    "Troque de sistema, não de identidade.": "Change systems, not your identity.",
    (
        "Salve perfis e credenciais num bundle verificável e restaure na próxima "
        "distro sem depender de sincronização na nuvem."
    ): (
        "Save profiles and credentials in a verifiable bundle and restore them "
        "on the next distro without relying on cloud sync."
    ),
    "ESTE COMPUTADOR": "THIS COMPUTER",
    "Detectando ambiente…": "Detecting environment…",
    "{} navegador(es) · {} conta(s) de IA · {} destino(s) candidato(s)": (
        "{} browser profile(s) · {} AI account(s) · {} candidate destination(s)"
    ),
    "⇣  Criar backup": "⇣  Create backup",
    (
        "Escolha perfis, contas e destinos. O bundle só é aceito após verificar "
        "os checksums."
    ): (
        "Choose profiles, accounts, and destinations. The bundle is accepted "
        "only after its checksums are verified."
    ),
    "Começar backup": "Start backup",
    "⇡  Restaurar": "⇡  Restore",
    (
        "Abra um bundle, confira o manifesto e aplique raw ou converta entre "
        "engines."
    ): (
        "Open a bundle, review its manifest, then apply it raw or convert it "
        "between engines."
    ),
    "Abrir restore": "Open restore",
    "Retomar pendente": "Resume pending",
    "Visão geral": "Overview",
    "A detecção ainda está terminando.": "Detection is still finishing.",
    "O que deve atravessar a formatação?": "What should survive the reinstall?",
    (
        "A cópia raw preserva o navegador; o formato neutro permite conversão "
        "entre engines."
    ): (
        "A raw copy preserves the browser; the neutral format allows conversion "
        "between engines."
    ),
    "Navegadores": "Browsers",
    "Nenhum perfil encontrado.": "No profile found.",
    "Contas de IA": "AI accounts",
    "Nenhuma conta encontrada.": "No account found.",
    "Sistema": "System",
    "Chaves SSH": "SSH keys",
    "Chaves GPG": "GPG keys",
    "Dotfiles conhecidos": "Known dotfiles",
    "Inventário de pacotes": "Package inventory",
    "Voltar": "Back",
    "Continuar": "Continue",
    "Novo backup": "New backup",
    "Escolha onde o bundle vai sobreviver.": "Choose where the bundle will survive.",
    "Você pode gravar e verificar o mesmo snapshot em vários discos.": (
        "You can write and verify the same snapshot on multiple drives."
    ),
    "Destinos": "Destinations",
    "Escolha um destino de backup": "Choose a backup destination",
    "+ Adicionar pasta": "+ Add folder",
    "Criar subpasta…": "Create subfolder…",
    "Criar subpasta": "Create subfolder",
    "Nome da nova pasta:": "New folder name:",
    "Escolha a pasta onde criar a subpasta": (
        "Choose the folder in which to create the subfolder"
    ),
    "Selecione um destino ou adicione uma pasta primeiro.": (
        "Select a destination or add a folder first."
    ),
    "A pasta já existe: {}": "The folder already exists: {}",
    "Não foi possível criar a pasta: {}": "Could not create the folder: {}",
    "O nome deve identificar uma única pasta.": (
        "The name must identify a single folder."
    ),
    "O nome da pasta não pode ficar vazio.": "The folder name cannot be empty.",
    "Esse nome de pasta é reservado no Windows.": (
        "That folder name is reserved on Windows."
    ),
    "O destino selecionado não é uma pasta: {}": (
        "The selected destination is not a folder: {}"
    ),
    "Remover": "Remove",
    "Partição-cofre…": "Vault partition…",
    "Cifrar conteúdo sensível com senha": "Encrypt sensitive content with a password",
    "Senha": "Password",
    "Confirmar": "Confirm",
    "Adicione pelo menos um destino.": "Add at least one destination.",
    "Informe duas senhas iguais e não vazias.": (
        "Enter two matching, non-empty passwords."
    ),
    "Ver plano": "View plan",
    "Criar backup": "Create backup",
    "Destino e proteção": "Destination and protection",
    "Distrohop · Partição-cofre": "Distrohop · Vault partition",
    "⚠  Uma cópia dentro do mesmo disco não é invencível.": (
        "⚠  A copy on the same drive is not invincible."
    ),
    (
        "Uma partição-cofre só sobrevive se, ao instalar a nova distro, você "
        "escolher particionamento manual e NÃO marcar esta partição para formatar. "
        "Se escolher apagar o disco inteiro, o instalador destrói o cofre junto. "
        "Se não pretende usar particionamento manual, use um destino externo."
    ): (
        "A vault partition survives only if you choose manual partitioning while "
        "installing the new distro and DO NOT mark this partition for formatting. "
        "If you erase the entire drive, the installer destroys the vault too. "
        "Use an external destination if you will not use manual partitioning."
    ),
    "Disco GPT": "GPT drive",
    "Tamanho em GiB": "Size in GiB",
    "Segunda cópia já verificada": "Second copy already verified",
    "Procurar": "Browse",
    "Escolha a segunda cópia Distrohop": "Choose the second Distrohop copy",
    "Digite exatamente: {}": "Type exactly: {}",
    "Cancelar": "Cancel",
    "A confirmação precisa ser digitada por extenso e sem alterações.": (
        "The confirmation must be typed in full without changes."
    ),
    "Informe um tamanho positivo em GiB.": "Enter a positive size in GiB.",
    "Informe o disco e a segunda cópia íntegra.": (
        "Provide the drive and the intact second copy."
    ),
    "Revalidando partição-cofre": "Revalidating vault partition",
    (
        "A GUI não eleva o aplicativo inteiro. Revise o plano e execute "
        "`sudo distrohop vault create ... --execute` no terminal."
    ): (
        "The GUI does not elevate the entire application. Review the plan and run "
        "`sudo distrohop vault create ... --execute` in the terminal."
    ),
    "Partição-cofre criada e verificada": "Vault partition created and verified",
    "Criar (root)": "Create (root)",
    "Planejando backup": "Planning backup",
    "• Plano validado; iniciando captura.": "• Plan validated; starting capture.",
    "Backup concluído": "Backup completed",
    "Abra o bundle de origem.": "Open the source bundle.",
    "O manifesto permanece legível mesmo quando o conteúdo está cifrado.": (
        "The manifest remains readable even when the content is encrypted."
    ),
    "Pasta do bundle": "Bundle folder",
    "Escolha um bundle Distrohop": "Choose a Distrohop bundle",
    (
        "Nada será alterado nesta etapa: primeiro conferimos manifesto e "
        "checksums."
    ): (
        "Nothing is changed at this stage: the manifest and checksums are checked "
        "first."
    ),
    "Escolha a pasta do bundle.": "Choose the bundle folder.",
    "Validando bundle": "Validating bundle",
    "checksums do bundle não conferem": "bundle checksums do not match",
    "Validar bundle": "Validate bundle",
    "Restaurar": "Restore",
    "Retome depois da preparação.": "Resume after preparation.",
    (
        "Use após aplicar a declaração do sistema ou reiniciar uma distribuição "
        "atômica."
    ): (
        "Use this after applying the system declaration or rebooting an atomic "
        "distribution."
    ),
    "Bundle com estado pendente": "Bundle with pending state",
    "Escolha o bundle pendente": "Choose the pending bundle",
    "Senha, se o bundle estiver cifrado": "Password, if the bundle is encrypted",
    "Validando retomada": "Validating resume",
    "Restore retomado e concluído": "Restore resumed and completed",
    "Retomar": "Resume",
    "Retomar restore": "Resume restore",
    "Escolha origem e destino.": "Choose source and destination.",
    "{} · {} arquivo(s) verificado(s)": "{} · {} verified file(s)",
    "Bundle cifrado": "Encrypted bundle",
    "Bundle sem cifra": "Unencrypted bundle",
    "Perfil de origem": "Source profile",
    "Navegador de destino": "Destination browser",
    "Pasta do perfil de destino": "Destination profile folder",
    "Senha do bundle": "Bundle password",
    "Instalar navegador se estiver ausente": "Install browser if missing",
    "Escolha os navegadores de origem e destino.": (
        "Choose the source and destination browsers."
    ),
    "Configurar restore": "Configure restore",
    "Planejando restore": "Planning restore",
    "Restore concluído": "Restore completed",
    "Aguardando preparação externa": "Waiting for external preparation",
    "Preparação necessária.": "Preparation required.",
    (
        "O perfil ainda não foi alterado. Siga a orientação e retome quando o "
        "navegador estiver disponível."
    ): (
        "The profile has not been changed. Follow the guidance and resume when "
        "the browser is available."
    ),
    "⌁  Restore pausado com segurança": "⌁  Restore paused safely",
    "Instruções: {}": "Instructions: {}",
    "Execute resume quando estiver pronto.": "Run resume when you are ready.",
    "Abrir retomada": "Open resume",
    "Restore pendente": "Pending restore",
    (
        "A janela continua responsiva enquanto o motor trabalha em segundo plano."
    ): "The window stays responsive while the engine works in the background.",
    "Plano somente leitura": "Read-only plan",
    "Nenhum arquivo foi alterado. Revise as origens e gravações abaixo.": (
        "No file was changed. Review the sources and planned writes below."
    ),
    "LEITURAS": "READS",
    "GRAVAÇÕES PLANEJADAS": "PLANNED WRITES",
    "AVISO: {}": "WARNING: {}",
    "Voltar ao início": "Back to home",
    "Plano do cofre — somente leitura": "Vault plan — read-only",
    "Nenhum setor foi alterado. A execução revalida tudo outra vez.": (
        "No sector was changed. Execution revalidates everything again."
    ),
    "Voltar aos destinos": "Back to destinations",
    "Partição-cofre": "Vault partition",
    "Tudo conferido.": "Everything verified.",
    "{}. A cópia anterior foi preservada quando havia um perfil no destino.": (
        "{}. The previous copy was preserved when a profile already existed at "
        "the destination."
    ),
    "Espaço insuficiente no destino de trabalho. O backup foi cancelado antes de "
    "publicar qualquer bundle. Libere espaço ou escolha outro destino.": (
        "Not enough space in the working destination. The backup was cancelled "
        "before publishing any bundle. Free some space or choose another destination."
    ),
    "Iniciando detecção": "Starting detection",
    "Plataforma detectada": "Platform detected",
    "Detecção concluída": "Detection completed",
    "Plano de backup criado": "Backup plan created",
    "Espaço de trabalho validado": "Working space validated",
    "Iniciando captura": "Starting capture",
    "Capturando perfil": "Capturing profile",
    "Engine desconhecida; perfil pulado": "Unknown engine; profile skipped",
    "Capturando contas de IA": "Capturing AI accounts",
    "Capturando dados do sistema": "Capturing system data",
    "Montando bundle": "Building bundle",
    "Gravando e verificando destinos": "Writing and verifying destinations",
    "Backup concluído e verificado": "Backup completed and verified",
    "Plano de restauração criado": "Restore plan created",
    "Gerando orientação declarativa": "Generating declarative guidance",
    "Instalando navegador na imagem atômica": (
        "Installing browser in the atomic image"
    ),
    "Restore aguardando preparação e resume": (
        "Restore waiting for preparation and resume"
    ),
    "Instalando navegador": "Installing browser",
    "Verificando bundle": "Verifying bundle",
    "Aplicando perfil raw": "Applying raw profile",
    "Convertendo perfil entre engines": "Converting profile between engines",
    "Senhas não são importadas automaticamente entre engines.": (
        "Passwords are not imported automatically between engines."
    ),
    "Alguns sites podem exigir novo login por dados presos ao localStorage.": (
        "Some sites may require a new login because of data tied to localStorage."
    ),
    (
        "A distribuição é declarativa: siga o arquivo de orientação e use resume."
    ): "The distribution is declarative: follow the guidance file and use resume.",
    (
        "A instalação atômica exige reboot; o perfil só será aplicado por resume."
    ): "Atomic installation requires a reboot; the profile is applied only by resume.",
    "reinicie e execute distrohop resume": "reboot and run distrohop resume",
    "aplique a declaração e execute distrohop resume": (
        "apply the declaration and run distrohop resume"
    ),
    "Espaço insuficiente em {}. O bundle precisa de cerca de {} e há {} livres.": (
        "Not enough space at {}. The bundle needs about {}, with {} available."
    ),
    (
        "Espaço insuficiente nos destinos. A montagem precisa de cerca de {} e o "
        "maior espaço livre encontrado foi {} em {}."
    ): (
        "Not enough space in the destinations. Staging needs about {}, and the "
        "largest available space found was {} at {}."
    ),
    "{} está aberto; feche todas as janelas antes do restore": (
        "{} is open; close every window before restoring"
    ),
    "checksums do payload decriptado não conferem": (
        "decrypted payload checksums do not match"
    ),
    "Distribuição desconhecida: instalações futuras usarão Flatpak ou orientação manual.": (
        "Unknown distribution: future installations will use Flatpak or manual guidance."
    ),
    (
        "Não foi possível inventariar discos com lsblk; destinos ainda não foram "
        "validados."
    ): "Could not inventory drives with lsblk; destinations have not been validated yet.",
    "plataforma de backup não suportada": "unsupported backup platform",
    "o mesmo destino foi informado mais de uma vez": (
        "the same destination was provided more than once"
    ),
    "selecione pelo menos um destino para gravar o backup": (
        "select at least one destination to write the backup"
    ),
    "o backup cifrado exige senha": "an encrypted backup requires a password",
    "senha fornecida para um backup sem cifra": (
        "a password was provided for an unencrypted backup"
    ),
    "nenhum perfil do bundle corresponde à seleção": (
        "no bundle profile matches the selection"
    ),
    "perfil de destino não detectado; informe --target-profile": (
        "destination profile not detected; provide --target-profile"
    ),
    "plataforma de restore não suportada": "unsupported restore platform",
    "a imagem atômica ainda está no mesmo boot; reinicie antes de resume": (
        "the atomic image is still on the same boot; reboot before resume"
    ),
    "há vários perfis de destino; informe --target-profile: {}": (
        "multiple destination profiles exist; provide --target-profile: {}"
    ),
    "engine do navegador de destino {} não é conhecida": (
        "destination browser engine {} is unknown"
    ),
    "O gerenciador {} era esperado, mas o comando não foi localizado.": (
        "Package manager {} was expected, but its command was not found."
    ),
    "perfis não detectados: {}": "profiles not detected: {}",
    "contas de IA não detectadas: {}": "AI accounts not detected: {}",
    "selecione um perfil de origem com --browser e --source-profile: {}": (
        "select a source profile with --browser and --source-profile: {}"
    ),
    "o perfil selecionado não contém dados {}": (
        "the selected profile contains no {} data"
    ),
    "{} não está instalado; repita com --install": (
        "{} is not installed; repeat with --install"
    ),
    "a preparação ainda não está pronta: {}": "preparation is not ready yet: {}",
    "bundle publicado falhou na verificação: {}": (
        "published bundle failed verification: {}"
    ),
    "versão Chromium de cifra não suportada": (
        "unsupported Chromium encryption version"
    ),
    "openssl não está disponível": "openssl is not available",
    "OpenSSL não decriptou o valor: {}": "OpenSSL did not decrypt the value: {}",
    "OpenSSL não cifrou o valor: {}": "OpenSSL did not encrypt the value: {}",
    "cookies Chromium não lidos: {}": "Chromium cookies could not be read: {}",
    "senhas Chromium não lidas: {}": "Chromium passwords could not be read: {}",
    "favoritos Chromium não lidos: {}": "Chromium bookmarks could not be read: {}",
    "senha {} pulada: {}": "password {} skipped: {}",
    "DPAPI só está disponível no Windows": "DPAPI is available only on Windows",
    "Local State não contém chave Chromium": (
        "Local State does not contain a Chromium key"
    ),
    "perfil contém chave app-bound desconhecida": (
        "profile contains an unknown app-bound key"
    ),
    "perfil usa App-Bound Encryption": "profile uses App-Bound Encryption",
    "chave Chromium não tem prefixo DPAPI": (
        "Chromium key does not have a DPAPI prefix"
    ),
    "valor usa App-Bound Encryption v20 e não pode ser aberto por DPAPI": (
        "value uses App-Bound Encryption v20 and cannot be opened with DPAPI"
    ),
    "chave Chromium em base64 inválida": "invalid base64 Chromium key",
    (
        "perfil usa App-Bound Encryption; cookies podem exigir consentimento do "
        "próprio navegador"
    ): (
        "profile uses App-Bound Encryption; cookies may require consent from the "
        "browser itself"
    ),
    "valor AES-GCM sem chave DPAPI disponível": (
        "AES-GCM value has no available DPAPI key"
    ),
    "CryptUnprotectData falhou com código {}": (
        "CryptUnprotectData failed with code {}"
    ),
    "DPAPI devolveu chave AES de tamanho inválido: {}": (
        "DPAPI returned an invalid AES key size: {}"
    ),
    "cookie Chromium com tag GCM inválida": "Chromium cookie has an invalid GCM tag",
    "Local State inválido: {}": "invalid Local State: {}",
    "chave Chromium não aberta: {}": "Chromium key could not be opened: {}",
    "tag AES-GCM inválida": "invalid AES-GCM tag",
    "AES exige chave de 128, 192 ou 256 bits": (
        "AES requires a 128, 192, or 256-bit key"
    ),
    "chave Chromium precisa ter 32 bytes": "Chromium key must be 32 bytes",
    "{}: {} caminho(s) /nix/store convertido(s) para comando portátil": (
        "{}: {} /nix/store path(s) converted to a portable command"
    ),
    "{} não encontrado": "{} not found",
    "não foi possível capturar o inventário nativo de pacotes ({})": (
        "could not capture the native package inventory ({})"
    ),
    "{} contém referência /nix/store não sanitizada; revise manualmente": (
        "{} contains an unsanitized /nix/store reference; review it manually"
    ),
    "libnss3 não foi localizada": "libnss3 was not found",
    "NSS não abriu o perfil; senha primária ou biblioteca incompatível": (
        "NSS did not open the profile; primary password or incompatible library"
    ),
    "NSS recusou a credencial; talvez haja senha primária": (
        "NSS rejected the credential; a primary password may be set"
    ),
    "libnss3 não expõe as funções necessárias": (
        "libnss3 does not expose the required functions"
    ),
    "cookies Firefox não lidos: {}": "Firefox cookies could not be read: {}",
    "favoritos Firefox não lidos: {}": "Firefox bookmarks could not be read: {}",
    "Sem título": "Untitled",
    "ocupado": "busy",
    "mudando continuamente": "changing continuously",
    "timeout_seconds precisa ser positivo": "timeout_seconds must be positive",
    "ciclo de symlink ignorado": "symlink cycle ignored",
    "{}: ciclo de symlink ignorado": "{}: symlink cycle ignored",
    "O banco {} permaneceu {} por mais de {:.1f}s. Feche o navegador e tente novamente.": (
        "Database {} remained {} for more than {}s. Close the browser and try again."
    ),
    "{}: snapshot SQLite falhou: {}": "{}: SQLite snapshot failed: {}",
    "data/browsers.json não contém a lista linux": (
        "data/browsers.json does not contain the Linux list"
    ),
    "data/browsers.json inválido: {}": "invalid data/browsers.json: {}",
    "Plataforma não suportada:": "Unsupported platform:",
    "desconhecida": "unknown",
    "Linux genérico": "Generic Linux",
    "restore neutro exige engines diferentes": (
        "neutral restore requires different engines"
    ),
    "chave do perfil Chromium Windows não está disponível": (
        "the Windows Chromium profile key is unavailable"
    ),
    (
        "Senhas cross-engine exigem importação manual de distrohop-logins.csv ou "
        "novo login."
    ): (
        "Cross-engine passwords require manual import from distrohop-logins.csv "
        "or a new login."
    ),
    "tabela {} não tem colunas compatíveis": "table {} has no compatible columns",
    "dados neutros não encontrados: {}": "neutral data not found: {}",
    "engine de destino sem suporte: {}": "unsupported destination engine: {}",
    "cookies.jsonl linha {} não é um objeto": (
        "cookies.jsonl line {} is not an object"
    ),
    "perfil de destino não é um diretório: {}": (
        "destination profile is not a directory: {}"
    ),
    "cookies.jsonl inválido na linha {}: {}": (
        "invalid cookies.jsonl at line {}: {}"
    ),
    "perfil raw não encontrado: {}": "raw profile not found: {}",
    "nenhum pacote seguro disponível para {}; instale manualmente": (
        "no safe package is available for {}; install it manually"
    ),
    "não há receita de instalação para {}": "no installation recipe exists for {}",
    "instalação falhou com status {}: {}": "installation failed with status {}: {}",
    "data/packages.json inválido: {}": "invalid data/packages.json: {}",
    "estado de resume usa formato desconhecido": (
        "resume state uses an unknown format"
    ),
    "estado de resume inválido ou ausente: {}": (
        "invalid or missing resume state: {}"
    ),
    "estado de resume incompleto: {}": "incomplete resume state: {}",
    "WinGet/Chocolatey ou ID verificado não disponível para {}": (
        "WinGet/Chocolatey or a verified ID is unavailable for {}"
    ),
    "não há receita Windows para {}": "no Windows installation recipe exists for {}",
    "instalação Windows falhou com status {}": (
        "Windows installation failed with status {}"
    ),
    "manifest.json não contém o mapa de arquivos": (
        "manifest.json does not contain the file map"
    ),
    "manifest.json inválido: {}": "invalid manifest.json: {}",
    "a senha não pode conter quebra de linha": (
        "the password cannot contain a line break"
    ),
    "bundle AES-GCM truncado": "truncated AES-GCM bundle",
    "a senha de cifra não pode ser vazia": "the encryption password cannot be empty",
    "iterações PBKDF2 fora do limite seguro": (
        "PBKDF2 iterations are outside the safe limit"
    ),
    "tamanho de bloco AES-GCM fora do limite": (
        "AES-GCM block size is outside the limit"
    ),
    "senha incorreta ou bundle AES-GCM adulterado": (
        "incorrect password or tampered AES-GCM bundle"
    ),
    "OpenSSL falhou: {}": "OpenSSL failed: {}",
    "formato de bundle AES-GCM desconhecido": "unknown AES-GCM bundle format",
    "iterações PBKDF2 inválidas no bundle": "invalid PBKDF2 iterations in bundle",
    "tamanho de bloco inválido no bundle": "invalid block size in bundle",
    "tipo de arquivo inseguro no bundle cifrado": (
        "unsafe file type in encrypted bundle"
    ),
    "caminho inseguro no arquivo cifrado": "unsafe path in encrypted archive",
    "dados inesperados após o fim do bundle AES-GCM": (
        "unexpected data after the end of the AES-GCM bundle"
    ),
    "DRY-RUN — nenhum disco será alterado.": "DRY RUN — no drive will be changed.",
    "Estratégia: {}": "Strategy: {}",
    "Disco: {}": "Drive: {}",
    "Nova partição: {} ({} bytes)": "New partition: {} ({} bytes)",
    "Segunda cópia íntegra: {}": "Intact second copy: {}",
    "Redução Btrfs planejada: {} bytes em {}": (
        "Planned Btrfs reduction: {} bytes at {}"
    ),
    "COMANDOS PLANEJADOS:": "PLANNED COMMANDS:",
    "copiar e verificar {} no cofre": "copy and verify {} in the vault",
    "a tabela GPT não tem entrada de partição livre": (
        "the GPT table has no free partition entry"
    ),
    "a segunda cópia não é um bundle íntegro": (
        "the second copy is not an intact bundle"
    ),
    "a segunda cópia está no mesmo disco escolhido": (
        "the second copy is on the selected drive"
    ),
    "a partição-cofre exige tabela GPT existente": (
        "the vault partition requires an existing GPT table"
    ),
    "a tabela contém partição com tamanho inválido": (
        "the table contains a partition with an invalid size"
    ),
    "já existe uma entrada GPT com o nome da partição-cofre": (
        "a GPT entry with the vault partition name already exists"
    ),
    "não há espaço contíguo suficiente no final do disco": (
        "there is not enough contiguous space at the end of the drive"
    ),
    "o plano tentou alterar boot/fstab/ordem": (
        "the plan attempted to change boot, fstab, or partition order"
    ),
    "btrfs não informou espaço livre estimado em bytes": (
        "Btrfs did not report estimated free space in bytes"
    ),
    "partição-cofre existe somente no Linux": (
        "the vault partition is available only on Linux"
    ),
    "o alvo não é um dispositivo de bloco": "the target is not a block device",
    "a quantidade de partições mudou de forma inesperada; mkfs foi bloqueado": (
        "the partition count changed unexpectedly; mkfs was blocked"
    ),
    "a nova entrada GPT não corresponde ao plano; mkfs foi bloqueado": (
        "the new GPT entry does not match the plan; mkfs was blocked"
    ),
    "confirmação por extenso não confere": "the full confirmation does not match",
    "criação do cofre exige root; revise o dry-run e execute a CLI com sudo": (
        "vault creation requires root; review the dry run and run the CLI with sudo"
    ),
    "a segunda cópia deixou de ser íntegra": (
        "the second copy is no longer intact"
    ),
    "a segunda cópia deixou de ser independente": (
        "the second copy is no longer independent"
    ),
    "a tabela de partições mudou desde o planejamento": (
        "the partition table changed after planning"
    ),
    "o dispositivo planejado já existe; a tabela mudou": (
        "the planned device already exists; the table changed"
    ),
    "partições sobrepostas ou fora dos limites GPT": (
        "partitions overlap or lie outside GPT bounds"
    ),
    "espaço livre GPT insuficiente para o cofre e a margem": (
        "insufficient GPT free space for the vault and safety margin"
    ),
    (
        "o espaço livre é insuficiente e a última partição não pôde ser "
        "inspecionada"
    ): (
        "free space is insufficient and the last partition could not be inspected"
    ),
    (
        "encolhimento automático recusado: o filesystem não é btrfs; use live "
        "USB/GParted"
    ): (
        "automatic shrinking refused: the filesystem is not Btrfs; use a live "
        "USB or GParted"
    ),
    "Btrfs com múltiplos dispositivos não é encolhido automaticamente": (
        "multi-device Btrfs is not shrunk automatically"
    ),
    "há balance Btrfs em andamento": "a Btrfs balance is running",
    "há scrub Btrfs em andamento": "a Btrfs scrub is running",
    "há snapshot/send Btrfs em andamento": "a Btrfs snapshot/send is running",
    "espaço livre real do Btrfs é menor que o tamanho pedido + 20%": (
        "actual Btrfs free space is less than the requested size plus 20%"
    ),
    "a redução deixaria a partição Btrfs inválida": (
        "shrinking would leave the Btrfs partition invalid"
    ),
    "não foi possível calcular uma redução segura": (
        "could not calculate a safe reduction"
    ),
    "JSON do lsblk inválido": "invalid lsblk JSON",
    "findmnt não confirmou o Btrfs montado": (
        "findmnt did not confirm the mounted Btrfs filesystem"
    ),
    "uma partição existente divergiu do plano; mkfs foi bloqueado": (
        "an existing partition differs from the plan; mkfs was blocked"
    ),
    "a partição Btrfs de origem desapareceu": "the source Btrfs partition disappeared",
    "o Btrfs passou a usar múltiplos dispositivos": (
        "Btrfs started using multiple devices"
    ),
    "uma operação Btrfs começou depois do planejamento": (
        "a Btrfs operation started after planning"
    ),
    "o espaço livre Btrfs caiu depois do planejamento": (
        "Btrfs free space decreased after planning"
    ),
    "o label ext4 não corresponde ao cofre planejado": (
        "the ext4 label does not match the planned vault"
    ),
    "a cópia escrita no cofre falhou na verificação": (
        "the copy written to the vault failed verification"
    ),
    "partição {} não pertence a {}": "partition {} does not belong to {}",
    "número de partição não reconhecido: {}": "unrecognized partition number: {}",
    "tamanho de setor não suportado: {}": "unsupported sector size: {}",
    "entrada de partição inválida": "invalid partition entry",
    "adicionar uma única entrada GPT no espaço livre": (
        "add a single GPT entry in free space"
    ),
    "informar a nova partição ao kernel": "inform the kernel about the new partition",
    "aguardar a criação do dispositivo": "wait for device creation",
    "formatar somente a nova partição como ext4": (
        "format only the new partition as ext4"
    ),
    "confirmação incorreta; digite exatamente: {}": (
        "incorrect confirmation; type exactly: {}"
    ),
    "{} Digite exatamente: {}": "{} Type exactly: {}",
    "{} falhou: {}": "{} failed: {}",
    "mount {} <temporário>": "mount {} <temporary>",
    "umount <temporário>": "umount <temporary>",
    "tabela de partições inválida: {}": "invalid partition table: {}",
    "reduzir o Btrfs antes da partição": "shrink Btrfs before the partition",
    "reduzir somente o final da partição Btrfs": (
        "shrink only the end of the Btrfs partition"
    ),
    "atualizar o tamanho no kernel": "update the size in the kernel",
    "{} não pôde ser executado: {}": "{} could not be executed: {}",
    "disco não encontrado: {}": "drive not found: {}",
    "o kernel não criou {}; reinicie e não formate nada": (
        "the kernel did not create {}; reboot and do not format anything"
    ),
    "criação do cofre falhou: {}": "vault creation failed: {}",
    "o destino {} não suporta permissões privadas 600/700; use --encrypt ou outro sistema de arquivos": (
        "destination {} does not support private 600/700 permissions; use --encrypt "
        "or another filesystem"
    ),
    "checksum pós-escrita divergiu em {}": "post-write checksum mismatch at {}",
    "Distrohop · antivírus": "Distrohop · antivirus",
    "Proteção transparente antes de começar": "Transparent protection before starting",
    "Continuar sem exclusão": "Continue without exclusion",
    "Habilitar exclusão desta pasta": "Enable exclusion for this folder",
    (
        "Este app lê cookies e senhas do navegador para migrar seus logins. "
        "Isso se parece com o comportamento de um infostealer e o Defender "
        "pode bloquear a operação.\n\n"
        "Habilitar libera somente esta pasta:\n{}\n\n"
        "O Windows mostrará um pedido UAC. O app inteiro nunca roda como "
        "administrador e o antivírus nunca é desativado."
    ): (
        "This app reads browser cookies and passwords to migrate your logins. "
        "That resembles infostealer behavior, so Defender may block the "
        "operation.\n\n"
        "Enabling this allows only this folder:\n{}\n\n"
        "Windows will show a UAC prompt. The entire app never runs as "
        "administrator, and antivirus protection is never disabled."
    ),
    (
        "Antivírus detectado: {}\n\n"
        "Adicione esta pasta às exclusões pelo painel do seu antivírus:\n"
        "{}\n\n"
        "Clique em OK depois de concluir, ou Cancelar para sair."
    ): (
        "Antivirus detected: {}\n\n"
        "Add this folder to exclusions in your antivirus control panel:\n"
        "{}\n\n"
        "Click OK when finished, or Cancel to exit."
    ),
    "warnings": "warnings",
    "browser": "browser",
    "mode": "mode",
    "pending": "pending",
    "preparation": "preparation",
    "guidance": "guidance",
    "resume_state": "resume state",
    "next": "next",
}

_REVERSE = {english: portuguese for portuguese, english in PT_EN.items()}
_FORMATTED: Sequence[Tuple[str, str]] = tuple(
    (portuguese, english)
    for portuguese, english in PT_EN.items()
    if any(field is not None for _, field, _, _ in string.Formatter().parse(portuguese))
)
_PREFIXES = ("• ", "⚠ ", "✕ ", "✓  ", "⌁  ", "⇣  ", "⇡  ")


def normalize_language(value: Optional[str]) -> str:
    candidate = (value or "").strip().casefold().replace("-", "_")
    return "pt" if candidate.startswith("pt") else "en"


def system_language(environ: Optional[Mapping[str, str]] = None) -> str:
    env = os.environ if environ is None else environ
    explicit = env.get("DISTROHOP_LANGUAGE")
    if explicit:
        return normalize_language(explicit)
    # LANGUAGE/LANG best reflect the desktop language; build tools sometimes
    # force LC_ALL=C only for deterministic command output.
    for key in ("LANGUAGE", "LC_MESSAGES", "LANG", "LC_ALL"):
        if env.get(key):
            return normalize_language(env[key])
    if environ is not None:
        return "en"
    try:
        current = locale.getlocale()[0]
    except (TypeError, ValueError):
        current = None
    return normalize_language(current)


def _match_template(text: str, source: str) -> Optional[Tuple[str, ...]]:
    pattern = "^"
    for literal, field, _format_spec, _conversion in string.Formatter().parse(source):
        pattern += re.escape(literal)
        if field is not None:
            pattern += "(.*?)"
    pattern += "$"
    match = re.match(pattern, text, flags=re.DOTALL)
    return match.groups() if match else None


def _render_template(destination: str, values: Sequence[str]) -> str:
    rendered = []
    index = 0
    for literal, field, _format_spec, _conversion in string.Formatter().parse(
        destination
    ):
        rendered.append(literal)
        if field is not None:
            rendered.append(values[index])
            index += 1
    return "".join(rendered)


def translate(text: str, language: str) -> str:
    target = normalize_language(language)
    table = PT_EN if target == "en" else _REVERSE
    if text in table:
        return table[text]
    pairs = _FORMATTED if target == "en" else tuple(
        (english, portuguese) for portuguese, english in _FORMATTED
    )
    for source, destination in pairs:
        values = _match_template(text, source)
        if values is not None:
            return _render_template(
                destination,
                tuple(translate(value, target) for value in values),
            )
    for prefix in _PREFIXES:
        if text.startswith(prefix):
            translated = translate(text[len(prefix):], target)
            return prefix + translated
    leading = text[: len(text) - len(text.lstrip())]
    if leading:
        return leading + translate(text[len(leading):], target)
    return text


def translate_block(text: str, language: str) -> str:
    return "".join(
        translate(line[:-1], language) + "\n"
        if line.endswith("\n")
        else translate(line, language)
        for line in text.splitlines(keepends=True)
    )
