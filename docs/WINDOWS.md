# Suporte Windows

O Windows é selecionado antes de qualquer detecção Linux. `list`, `backup`,
`restore`, `resume`, a CLI e a GUI usam o mesmo motor das distribuições Linux,
com implementações próprias somente nos limites do sistema operacional.

## Inicialização e antivírus

`distrohop.bat` muda para a pasta do aplicativo, procura `py -3`, `python` ou
`python3` e executa `distrohop.bootstrap`. Sem Python, mostra o comando oficial
do WinGet e não tenta elevar ou instalar nada sozinho.

O bootstrap consulta o estado do Defender, exclusões e produtos registrados no
Security Center. Se o Defender estiver ativo, a janela oferece continuar sem
exclusão ou pedir a exclusão exata da pasta do Distrohop. Só o
`Add-MpPreference -ExclusionPath <pasta>` roda elevado, depois de clique e UAC;
o aplicativo nunca roda como administrador e nunca desativa proteção. AVs de
terceiros recebem instrução manual. A escolha fica em
`.distrohop-bootstrap.json`; `--reset-bootstrap` limpa esse consentimento.

Essas operações ficam registradas em `.distrohop-bootstrap.log`. Nenhuma
implementação consegue garantir antecipadamente a reputação de um binário em
Defender, Avast ou outro antivírus: assinatura de código e reputação do release
são etapas de distribuição, separadas das travas transparentes do aplicativo.

## Navegadores e credenciais

- Chromium: Brave, Chrome e Edge usam `%LOCALAPPDATA%`; Firefox e Zen usam
  `%APPDATA%` e `profiles.ini`.
- A chave Chromium clássica vem de `Local State`, perde o prefixo `DPAPI` e é
  aberta por `CryptUnprotectData`. Valores `v10`/`v11` usam nonce de 12 bytes e
  AES-256-GCM; os testes batem byte por byte com vetores NIST.
- Chrome/Edge com chave `APPB` ou valor `v20` são identificados como App-Bound.
  O app preserva raw e favoritos, pula somente o segredo que não consegue abrir
  e registra o aviso; não tenta contornar o serviço de proteção do navegador.
- Firefox/Zen reutilizam `key4.db` e `logins.json`; o carregador procura
  `nss3.dll` nas instalações usuais. Sem uma NSS compatível, cookies,
  favoritos e raw continuam, e somente senhas recebem aviso.
- Restore Chromium cross-engine cifra cookies com a chave DPAPI do perfil de
  destino. Senhas entre engines ficam em CSV para importação manual.

O restore recusa escrever enquanto o nome exato do processo do navegador
aparece em `tasklist`; correspondências parciais não bloqueiam.

## Instalação, volumes e cifra

WinGet usa IDs verificados e correspondência exata, aceitando explicitamente os
acordos necessários ao modo não interativo. Chocolatey é fallback quando já
está instalado. Os volumes vêm de `Get-Volume`; `C:` é marcado como sistema e
somente volumes fixos/removíveis secundários entram como candidatos.

Bundles cifrados no Windows usam o contêiner `DHG1`: PBKDF2-HMAC-SHA256,
AES-256-GCM em blocos independentes e um registro terminal autenticado. Isso
limita memória, detecta senha errada, alteração e truncamento. O
`manifest.json` continua em claro. A mesma implementação abre `DHG1` no Linux,
e o Windows também consegue abrir bundles OpenSSL produzidos no Linux quando o
binário `openssl` estiver disponível.

## Validação

Como o desenvolvimento ocorre em Linux, DPAPI, PowerShell, WinGet, volumes,
processos e bootstrap são cobertos por fixtures/mocks; AES-GCM usa vetores
conhecidos e perfis SQLite sintéticos. Um smoke test numa máquina Windows real
continua sendo obrigatório antes de publicar um release assinado.

Fontes primárias:

- [WinGet install](https://learn.microsoft.com/windows/package-manager/winget/install)
- [CryptUnprotectData](https://learn.microsoft.com/windows/win32/api/dpapi/nf-dpapi-cryptunprotectdata)
- [Get-MpComputerStatus](https://learn.microsoft.com/powershell/module/defender/get-mpcomputerstatus)
- [Add-MpPreference](https://learn.microsoft.com/powershell/module/defender/add-mppreference)
- [Chromium OSCrypt para Windows](https://chromium.googlesource.com/chromium/src/+/refs/heads/main/components/os_crypt/sync/os_crypt_win.cc)
