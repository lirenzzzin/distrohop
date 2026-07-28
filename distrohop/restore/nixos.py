"""Guided declarative restore and /nix/store-safe dotfile application."""

from __future__ import annotations

import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Mapping, Optional


NIX_ATTRIBUTES: Mapping[str, str] = {
    "brave": "brave",
    "chrome": "google-chrome",
    "chromium": "chromium",
    "edge": "microsoft-edge",
    "vivaldi": "vivaldi",
    "opera": "opera",
    "firefox": "firefox",
    "librewolf": "librewolf",
}


def _nixos_style(home: Path, etc_nixos: Path) -> Dict[str, str]:
    flake = etc_nixos / "flake.nix"
    configuration = etc_nixos / "configuration.nix"
    home_manager = home / ".config" / "home-manager" / "home.nix"
    if flake.is_file():
        return {"kind": "flake", "path": str(flake)}
    if configuration.is_file():
        return {"kind": "classic", "path": str(configuration)}
    if home_manager.is_file():
        return {"kind": "home-manager", "path": str(home_manager)}
    return {"kind": "unknown", "path": str(configuration)}


def build_nixos_markdown(
    browser_id: str,
    *,
    home: Path,
    etc_nixos: Path = Path("/etc/nixos"),
) -> str:
    style = _nixos_style(home, etc_nixos)
    attribute = NIX_ATTRIBUTES.get(browser_id)
    if attribute:
        package_line = "pkgs.{}".format(attribute)
        package_block = (
            "```nix\n"
            "environment.systemPackages = with pkgs; [\n"
            "  {}\n"
            "];\n"
            "```"
        ).format(package_line)
    else:
        package_block = (
            "Não foi encontrado um atributo Nix confiável para `{}`. "
            "Pesquise em https://search.nixos.org/packages e adicione o "
            "atributo confirmado à sua configuração."
        ).format(browser_id)
    if style["kind"] == "home-manager" and attribute:
        package_block = (
            "```nix\n"
            "home.packages = with pkgs; [\n"
            "  {}\n"
            "];\n"
            "```"
        ).format(package_line)
        command = "home-manager switch"
    elif style["kind"] == "flake":
        command = "sudo nixos-rebuild switch --flake /etc/nixos"
    else:
        command = "sudo nixos-rebuild switch"
    return """# Distrohop — preparação declarativa do navegador

O Distrohop **não instala pacotes imperativamente no NixOS**. Edite:

`{path}`

Adicione ou mescle este bloco:

{package_block}

Depois execute:

```sh
{command}
```

Quando o comando terminar e o navegador estiver disponível, volte ao bundle e
execute:

```sh
distrohop resume "{bundle_placeholder}"
```

O perfil só será aplicado depois que o Distrohop detectar o binário. Dotfiles
que apontam para `/nix/store` nunca serão sobrescritos: a versão restaurada fica
ao lado com sufixo `.distrohop-restore`.
""".format(
        path=style["path"],
        package_block=package_block,
        command=command,
        bundle_placeholder="<pasta-do-bundle>",
    )


def build_declarative_markdown(
    browser_id: str,
    os_info: Mapping[str, Any],
    *,
    home: Path,
    etc_nixos: Path = Path("/etc/nixos"),
) -> Dict[str, str]:
    family = str(os_info.get("family") or "")
    if family == "nixos":
        return {
            "filename": "NIXOS.md",
            "content": build_nixos_markdown(
                browser_id,
                home=home,
                etc_nixos=etc_nixos,
            ),
        }
    if family == "guix":
        return {
            "filename": "DECLARATIVE.md",
            "content": """# Distrohop — GNU Guix

O Distrohop não altera a declaração do sistema. Adicione o pacote do navegador
`{browser}` ao campo `packages` da configuração Guix, execute:

```sh
sudo guix system reconfigure /etc/config.scm
```

ou, para um perfil de usuário deliberadamente gerenciado:

```sh
guix install {browser}
```

Depois rode `distrohop resume "<pasta-do-bundle>"`.
""".format(browser=browser_id),
        }
    return {
        "filename": "DECLARATIVE.md",
        "content": """# Distrohop — sistema declarativo

A distribuição `{name}` gerencia aplicativos declarativamente. Instale
`{browser}` pela declaração oficial do sistema, aplique/reinicie conforme a
documentação da distribuição e rode:

```sh
distrohop resume "<pasta-do-bundle>"
```
""".format(
            name=os_info.get("name") or os_info.get("id") or family,
            browser=browser_id,
        ),
    }


def write_declarative_guidance(
    bundle: Path,
    browser_id: str,
    os_info: Mapping[str, Any],
    *,
    home: Path,
    etc_nixos: Path = Path("/etc/nixos"),
) -> Path:
    guidance = build_declarative_markdown(
        browser_id,
        os_info,
        home=home,
        etc_nixos=etc_nixos,
    )
    destination = Path(bundle) / guidance["filename"]
    temporary = destination.with_name(
        ".{}.{}.partial".format(destination.name, uuid.uuid4().hex)
    )
    try:
        temporary.write_text(guidance["content"], encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return destination


def _inside(path: Path, root: Path) -> bool:
    resolved = path.resolve(strict=False)
    base = root.resolve(strict=False)
    return resolved == base or base in resolved.parents


def restore_dotfile_guarded(
    source: Path,
    target: Path,
    *,
    nix_store_root: Path = Path("/nix/store"),
) -> Dict[str, str]:
    if not source.is_file():
        raise FileNotFoundError(str(source))
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink() and _inside(target, nix_store_root):
        destination = target.with_name(target.name + ".distrohop-restore")
        if destination.exists():
            raise FileExistsError(str(destination))
        shutil.copy2(source, destination)
        os.chmod(destination, 0o600)
        return {
            "status": "redirected",
            "target": str(destination),
            "managed_target": str(target),
        }
    staging = target.with_name(
        ".{}.{}.partial".format(target.name, uuid.uuid4().hex)
    )
    backup: Optional[Path] = None
    try:
        shutil.copy2(source, staging)
        os.chmod(staging, 0o600)
        if target.exists() or target.is_symlink():
            backup = target.with_name(
                "{}.distrohop-before-{}".format(
                    target.name,
                    time.strftime("%Y%m%d-%H%M%S"),
                )
            )
            if backup.exists():
                raise FileExistsError(str(backup))
            os.replace(target, backup)
        os.replace(staging, target)
    except Exception:
        if backup is not None and backup.exists() and not target.exists():
            os.replace(backup, target)
        raise
    finally:
        if staging.exists():
            staging.unlink()
    return {
        "status": "restored",
        "target": str(target),
        "previous": str(backup) if backup else "",
    }
