"""Command-line frontend."""

from __future__ import annotations

import argparse
import getpass
import json
import stat
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from distrohop.core.engine import (
    BackupPlan,
    default_selection,
    list_inventory,
    plan_backup,
    run_backup,
)
from distrohop.core.events import Event
from distrohop.core.selection import Selection


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="distrohop", description="Migre perfis e credenciais com segurança.")
    parser.add_argument("--cli", action="store_true", help=argparse.SUPPRESS)
    subparsers = parser.add_subparsers(dest="command")
    list_parser = subparsers.add_parser("list", help="lista plataforma, perfis, contas e destinos")
    list_parser.add_argument("--json", action="store_true", help="emite JSON para automação")
    backup = subparsers.add_parser("backup", help="captura dados Linux e gera bundle verificado")
    backup.add_argument(
        "--target",
        dest="targets",
        action="append",
        default=[],
        metavar="PASTA",
        help="destino do bundle; pode ser repetido",
    )
    backup.add_argument("--encrypt", action="store_true", help="cifra o payload com OpenSSL")
    backup.add_argument(
        "--password-file",
        metavar="ARQUIVO",
        help="lê a senha de arquivo privado; nunca aceita senha na linha de comando",
    )
    backup.add_argument("--dry-run", action="store_true", help="lista cada leitura e gravação sem escrever")
    backup.add_argument(
        "--browser-profile",
        dest="browser_profiles",
        action="append",
        metavar="PASTA",
        help="inclui somente este perfil detectado; pode ser repetido",
    )
    backup.add_argument(
        "--ai-account",
        dest="ai_accounts",
        action="append",
        metavar="PASTA",
        help="inclui somente esta conta detectada; pode ser repetido",
    )
    backup.add_argument(
        "--extra",
        dest="extras",
        action="append",
        choices=("ssh", "gpg", "dotfiles", "packages"),
        help="inclui somente o extra indicado; pode ser repetido",
    )
    backup.add_argument("--no-browsers", action="store_true", help="não captura perfis de navegador")
    backup.add_argument("--no-ai", action="store_true", help="não captura contas de IA")
    backup.add_argument("--no-extras", action="store_true", help="não captura dados extras")
    return parser


def _value(value: object, fallback: str = "—") -> str:
    return fallback if value in (None, "", []) else str(value)


def render_inventory(data: Mapping[str, Any]) -> str:
    os_info = data["os"]
    lines = [
        f"Plataforma: {data['platform']}",
        f"Sistema: {_value(os_info.get('name'))}",
        f"Família: {_value(os_info.get('family_label'))}",
        f"Gerenciador: {_value(os_info.get('manager'))}",
        *(
            [f"Aplicativos: {os_info['app_manager']}"]
            if os_info.get("app_manager") and os_info.get("app_manager") != os_info.get("manager")
            else []
        ),
        f"Estratégia: {_value(os_info.get('strategy'))}",
    ]
    if data.get("warnings"):
        lines.extend(("", "Avisos:"))
        lines.extend(f"  ! {warning}" for warning in data["warnings"])
    lines.extend(("", "Navegadores:"))
    if not data["browsers"]:
        lines.append("  nenhum perfil encontrado")
    for browser in data["browsers"]:
        version = f", {browser['version']}" if browser.get("version") else ""
        status = ", dados locais" if browser.get("installed") is False else ""
        lines.append(f"  {browser['name']} ({browser['engine']}, {browser['packaging']}{version}{status})")
        if browser["profiles"]:
            for profile in browser["profiles"]:
                lines.append(f"    - {profile['name']}: {profile['path']}")
        else:
            label = "instalação encontrada, sem perfil" if browser.get("installed") else "dados encontrados"
            lines.append(f"    - {label}: {browser['path']}")
    lines.extend(("", "Contas de IA:"))
    if not data["ai_accounts"]:
        lines.append("  nenhuma encontrada")
    for account in data["ai_accounts"]:
        lines.append(f"  {account['tool']} [{account['slot']}]: {account['path']}")
    lines.extend(("", "Discos e destinos montados:"))
    if not data["disks"]:
        lines.append("  nenhum encontrado")
    for disk in data["disks"]:
        flags = []
        if disk["system"]:
            flags.append("SISTEMA")
        if disk["candidate"]:
            flags.append("destino válido")
        if disk["removable"]:
            flags.append("removível")
        suffix = f" [{' | '.join(flags)}]" if flags else ""
        label = f" {disk['label']}" if disk["label"] else ""
        mounts = ", ".join(disk["mountpoints"]) or "não montado"
        lines.append(f"  {disk['path']}{label} ({_value(disk['size'])}) → {mounts}{suffix}")
    return "\n".join(lines)


def render_backup_plan(plan: BackupPlan) -> str:
    lines = [
        "DRY-RUN — nenhum arquivo será escrito.",
        "Bundle: {}".format(plan.bundle_name),
        "Cifra: {}".format("sim" if plan.encrypted else "não"),
        "",
        "LEITURAS:",
    ]
    lines.extend("  {}".format(path) for path in plan.sources)
    if not plan.sources:
        lines.append("  nenhuma")
    lines.extend(("", "GRAVAÇÕES PLANEJADAS:"))
    lines.extend("  {}".format(path) for path in plan.outputs)
    if not plan.outputs:
        lines.append("  nenhuma")
    return "\n".join(lines)


def _event_to_stderr(event: Event) -> None:
    if event.kind == "plan":
        return
    prefix = "AVISO" if event.kind == "warn" else event.kind.upper()
    print("[{}] {}".format(prefix, event.message), file=sys.stderr)


def _selection_from_args(args: argparse.Namespace, inventory: Mapping[str, Any]) -> Selection:
    defaults = default_selection(inventory)
    browser_profiles = (
        ()
        if args.no_browsers
        else tuple(args.browser_profiles)
        if args.browser_profiles is not None
        else defaults.browser_profiles
    )
    ai_accounts = (
        ()
        if args.no_ai
        else tuple(args.ai_accounts)
        if args.ai_accounts is not None
        else defaults.ai_accounts
    )
    selected_extras = (
        ()
        if args.no_extras
        else tuple(args.extras)
        if args.extras is not None
        else defaults.extras
    )
    return Selection(
        browser_profiles=browser_profiles,
        ai_accounts=ai_accounts,
        extras=selected_extras,
    )


def _read_password(args: argparse.Namespace, parser: argparse.ArgumentParser) -> Optional[str]:
    if args.password_file and not args.encrypt:
        parser.error("--password-file só pode ser usado com --encrypt")
    if not args.encrypt:
        return None
    if args.password_file:
        try:
            password_path = Path(args.password_file)
            if stat.S_IMODE(password_path.stat().st_mode) & 0o077:
                parser.error("--password-file deve ter permissão 600 ou mais restritiva")
            password = password_path.read_text(encoding="utf-8").rstrip("\r\n")
        except OSError as error:
            parser.error("não foi possível ler --password-file: {}".format(error))
    else:
        try:
            password = getpass.getpass("Senha do bundle: ")
            confirmation = getpass.getpass("Confirme a senha: ")
        except (EOFError, KeyboardInterrupt):
            parser.error("não foi possível ler a senha; use --password-file")
        if password != confirmation:
            parser.error("as senhas não coincidem")
    if not password:
        parser.error("a senha não pode ser vazia")
    if "\n" in password or "\r" in password:
        parser.error("a senha não pode conter quebra de linha")
    return password


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "list":
        inventory = list_inventory()
        print(json.dumps(inventory, ensure_ascii=False, indent=2) if args.json else render_inventory(inventory))
        return 0
    if args.command == "backup":
        if args.password_file and not args.encrypt:
            parser.error("--password-file só pode ser usado com --encrypt")
        if not args.dry_run and not args.targets:
            parser.error("backup real exige pelo menos um --target")
        inventory = list_inventory(callback=_event_to_stderr)
        selection = _selection_from_args(args, inventory)
        try:
            plan = plan_backup(
                selection,
                tuple(Path(path) for path in args.targets),
                encrypted=args.encrypt,
                inventory=inventory,
                callback=_event_to_stderr,
            )
        except (RuntimeError, ValueError) as error:
            parser.error(str(error))
        if args.dry_run:
            print(render_backup_plan(plan))
            return 0
        password = _read_password(args, parser)
        if not args.encrypt:
            print(
                "AVISO: sem cifra, cookies, senhas, chaves e tokens ficam legíveis "
                "para quem acessar o destino.",
                file=sys.stderr,
            )
        try:
            result = run_backup(plan, password=password, callback=_event_to_stderr)
        except (OSError, RuntimeError, ValueError) as error:
            print("Erro: {}".format(error), file=sys.stderr)
            return 1
        print("Backup concluído e verificado:")
        for destination, digest in zip(
            result["destinations"], result["manifest_sha256"]
        ):
            print("  {}  manifest sha256={}".format(destination, digest))
        return 0
    else:
        parser.print_help()
        return 0
