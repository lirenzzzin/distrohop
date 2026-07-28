# Segurança

O Distrohop manipula cookies, senhas, tokens, chaves e tabelas de partição.
Relatórios de vulnerabilidade não devem ser abertos como issue pública.

Use **Security → Advisories → New draft security advisory** no repositório.
Não anexe bundles, perfis reais, `Local State`, bancos de cookies, logs com
caminhos pessoais, chaves, tokens ou imagens de disco. Produza uma fixture
sintética mínima e informe versão, plataforma e passos de reprodução.

## Garantias deliberadas

- Senhas de bundle nunca são aceitas na linha de comando.
- `manifest.json` é claro por projeto; o payload pode ser cifrado.
- O restore verifica checksums e preserva o perfil anterior.
- O bootstrap nunca desativa antivírus e nunca eleva o app inteiro.
- A partição-cofre exige confirmação longa, segunda cópia íntegra, dry-run e
  revalidação imediatamente antes de qualquer alteração.

## Limites

Perfis de navegador e bundles são segredos. Não os envie ao VirusTotal ou a
serviços públicos de análise. Releases públicos precisam de smoke test Windows,
assinatura de código e hashes publicados; reputação de antivírus não pode ser
garantida apenas pelo código-fonte.
