"""Single application API consumed by every user interface."""

from __future__ import annotations

import os
import re
import socket
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from distrohop.core.events import Event, EventCallback, discard_event
from distrohop.core.selection import Selection
from distrohop.capture import chromium_linux, extras, firefox
from distrohop.capture.profile_raw import list_files
from distrohop.detect import ai, browsers, disks, distro, windows
from distrohop.platform_ import current_platform
from distrohop.restore.apply_raw import apply_raw_profile
from distrohop.restore.apply_neutral import apply_neutral_profile
from distrohop.restore import installer
from distrohop.restore.processes import is_browser_running
from distrohop.vault.bundle import (
    assemble_bundle,
    materialize_payload,
    read_manifest,
    verify_bundle,
    verify_materialized_payload,
)
from distrohop.vault.crypto import sha256_file
from distrohop.vault.targets import publish_to_targets


@dataclass(frozen=True)
class BackupPlan:
    selection: Selection
    targets: Tuple[Path, ...]
    bundle_name: str
    inventory: Mapping[str, Any]
    home: Path
    sources: Tuple[str, ...]
    outputs: Tuple[str, ...]
    encrypted: bool = False


@dataclass(frozen=True)
class RestorePlan:
    bundle: Path
    component: Mapping[str, Any]
    target_profile: Path
    raw_prefix: str
    sources: Tuple[str, ...]
    outputs: Tuple[str, ...]
    encrypted: bool
    target_browser_id: str = ""
    target_engine: str = ""
    mode: str = "raw"
    warnings: Tuple[str, ...] = ()
    install_command: Tuple[str, ...] = ()


def list_inventory(
    *,
    callback: EventCallback = discard_event,
    system: Optional[str] = None,
    home: Optional[Path] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    """Return a read-only inventory. No probe writes to the system."""
    platform_name = current_platform(system)
    callback(Event("started", "Iniciando detecção", {"platform": platform_name}))
    env = os.environ if environ is None else environ
    user_home = home or Path(env.get("USERPROFILE" if platform_name == "windows" else "HOME", str(Path.home())))

    if platform_name == "linux":
        os_info = distro.detect()
        browser_items = browsers.detect_linux(user_home, environ=env)
        disk_items = disks.detect_linux()
    else:
        os_info = windows.detect(env)
        browser_items = browsers.detect_windows(env)
        disk_items = []
    callback(Event("step", "Plataforma detectada", {"os": os_info}))
    ai_items = ai.detect(user_home)
    warnings = []
    if os_info.get("strategy") == "fallback":
        warnings.append(
            "Distribuição desconhecida: instalações futuras usarão Flatpak ou orientação manual."
        )
    if os_info.get("manager") and os_info.get("manager_available") is False:
        warnings.append(
            "O gerenciador {} era esperado, mas o comando não foi localizado.".format(
                os_info["manager"]
            )
        )
    if platform_name == "linux" and not disk_items:
        warnings.append(
            "Não foi possível inventariar discos com lsblk; destinos ainda não foram validados."
        )
    for warning in warnings:
        callback(Event("warn", warning))
    callback(Event("done", "Detecção concluída"))
    return {
        "platform": platform_name,
        "os": os_info,
        "browsers": browser_items,
        "ai_accounts": ai_items,
        "disks": disk_items,
        "warnings": warnings,
    }


def default_selection(inventory: Mapping[str, Any]) -> Selection:
    return Selection(
        browser_profiles=tuple(
            str(profile["path"])
            for browser in inventory.get("browsers", [])
            for profile in browser.get("profiles", [])
        ),
        ai_accounts=tuple(
            str(account["path"]) for account in inventory.get("ai_accounts", [])
        ),
        extras=("ssh", "gpg", "dotfiles", "packages"),
    )


def _safe_name(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-")
    return safe or "item"


def _selected_profiles(
    inventory: Mapping[str, Any],
    selected: Sequence[str],
) -> Tuple[Tuple[Mapping[str, Any], Mapping[str, Any]], ...]:
    requested = set(selected)
    matches = []
    for browser in inventory.get("browsers", []):
        for profile in browser.get("profiles", []):
            if str(profile["path"]) in requested:
                matches.append((browser, profile))
    found = {str(profile["path"]) for _, profile in matches}
    missing = requested.difference(found)
    if missing:
        raise ValueError("perfis não detectados: {}".format(", ".join(sorted(missing))))
    return tuple(matches)


def _selected_accounts(
    inventory: Mapping[str, Any],
    selected: Sequence[str],
) -> Tuple[Mapping[str, str], ...]:
    requested = set(selected)
    matches = tuple(
        account
        for account in inventory.get("ai_accounts", [])
        if str(account["path"]) in requested
    )
    found = {str(account["path"]) for account in matches}
    missing = requested.difference(found)
    if missing:
        raise ValueError("contas de IA não detectadas: {}".format(", ".join(sorted(missing))))
    return matches


def _profile_destinations(
    profiles: Sequence[Tuple[Mapping[str, Any], Mapping[str, Any]]],
) -> Tuple[Tuple[Mapping[str, Any], Mapping[str, Any], Path], ...]:
    used = set()
    resolved = []
    for browser, profile in profiles:
        source = Path(str(profile["path"]))
        browser_name = _safe_name(str(browser["id"]))
        profile_name = _safe_name(str(profile.get("name") or source.name))
        relative = Path("browsers") / browser_name / profile_name
        suffix = 2
        while str(relative) in used:
            relative = Path("browsers") / browser_name / "{}-{}".format(
                profile_name, suffix
            )
            suffix += 1
        used.add(str(relative))
        resolved.append((browser, profile, relative))
    return tuple(resolved)


def plan_backup(
    selection: Selection,
    targets: Sequence[Path] = (),
    *,
    encrypted: bool = False,
    inventory: Optional[Mapping[str, Any]] = None,
    home: Optional[Path] = None,
    bundle_name: Optional[str] = None,
    callback: EventCallback = discard_event,
) -> BackupPlan:
    """Produce a complete read-only, file-by-file Linux backup plan."""
    data = inventory or list_inventory(callback=callback)
    if data.get("platform") != "linux":
        raise RuntimeError("a Fase 2 implementa backup somente no Linux")
    user_home = home or Path(os.environ.get("HOME", str(Path.home())))
    selected_profiles = _selected_profiles(data, selection.browser_profiles)
    selected_accounts = _selected_accounts(data, selection.ai_accounts)
    source_files = []
    output_relatives = []
    for browser, profile, relative in _profile_destinations(selected_profiles):
        source = Path(str(profile["path"]))
        profile_files = list_files(source)
        source_files.extend(profile_files)
        base = str(relative) + "/"
        output_relatives.extend(
            (
                base + "neutral/cookies.jsonl",
                base + "neutral/logins.csv",
                base + "neutral/bookmarks.html",
            )
        )
        for item in profile_files:
            path = Path(item)
            try:
                relative = path.relative_to(source)
            except ValueError:
                relative = Path(path.name)
            output_relatives.append(base + "raw/" + str(relative))
    for account in selected_accounts:
        source = Path(account["path"])
        account_files = list_files(source)
        source_files.extend(account_files)
        base = "ai/{}/{}/".format(
            _safe_name(account["tool"]),
            _safe_name(account["slot"]),
        )
        if source.is_dir():
            for item in account_files:
                output_relatives.append(base + str(Path(item).relative_to(source)))
        else:
            output_relatives.append(base.rstrip("/"))
    extra_files = extras.extras_sources(user_home, selection.extras)
    for source in extra_files:
        files = list_files(source)
        source_files.extend(files)
        if source == user_home / ".ssh":
            base = Path("system/ssh")
        elif source == user_home / ".gnupg":
            base = Path("system/gpg")
        else:
            base = Path("system/dotfiles") / source.relative_to(user_home)
        if source.is_dir():
            for item in files:
                output_relatives.append(str(base / Path(item).relative_to(source)))
        else:
            output_relatives.append(str(base))
    if "packages" in selection.extras:
        manager = str(data.get("os", {}).get("manager") or "")
        commands = extras.PACKAGE_COMMANDS.get(manager, ())
        source_files.extend(str(path) for path in extras.package_read_sources(manager))
        source_files.extend(
            "comando: {}".format(" ".join(command)) for _, command in commands
        )
        source_files.extend(
            "comando: {}".format(" ".join(command))
            for _, command in extras.UNIVERSAL_COMMANDS
        )
        output_relatives.append("system/packages/packages.json")
        output_relatives.extend(
            "system/packages/{}.txt".format(_safe_name(label))
            for label, _ in tuple(commands) + tuple(extras.UNIVERSAL_COMMANDS)
        )
    host = _safe_name(socket.gethostname())
    name = bundle_name or "distrohop-{}-{}".format(
        host, time.strftime("%Y%m%d-%H%M")
    )
    target_paths = tuple(Path(path) for path in targets)
    if len(set(target_paths)) != len(target_paths):
        raise ValueError("o mesmo destino foi informado mais de uma vez")
    if encrypted:
        output_relatives = ["manifest.json", "README.txt", "bundle.tar.enc"]
    else:
        output_relatives.extend(("manifest.json", "README.txt"))
    output_relatives = list(dict.fromkeys(output_relatives))
    outputs = tuple(
        str(target / name / relative)
        for target in target_paths
        for relative in output_relatives
    )
    if not target_paths:
        outputs = tuple("<destino>/{}/{}".format(name, item) for item in output_relatives)
    callback(
        Event(
            "plan",
            "Plano de backup criado",
            {"sources": len(source_files), "targets": len(target_paths), "encrypted": encrypted},
        )
    )
    return BackupPlan(
        selection=selection,
        targets=target_paths,
        bundle_name=name,
        inventory=data,
        home=user_home,
        sources=tuple(source_files),
        outputs=outputs,
        encrypted=encrypted,
    )


def run_backup(
    plan: BackupPlan,
    *,
    password: Optional[str] = None,
    callback: EventCallback = discard_event,
) -> Dict[str, Any]:
    """Execute a Linux backup plan and verify every published destination."""
    if not plan.targets:
        raise ValueError("selecione pelo menos um destino para gravar o backup")
    if plan.encrypted and not password:
        raise ValueError("o backup cifrado exige senha")
    if not plan.encrypted and password:
        raise ValueError("senha fornecida para um backup sem cifra")
    if plan.inventory.get("platform") != "linux":
        raise RuntimeError("a Fase 2 implementa backup somente no Linux")
    callback(Event("started", "Iniciando captura", {"bundle": plan.bundle_name}))
    selected_profiles = _selected_profiles(plan.inventory, plan.selection.browser_profiles)
    selected_accounts = _selected_accounts(plan.inventory, plan.selection.ai_accounts)
    captured_browsers = []
    capture_warnings = []
    with tempfile.TemporaryDirectory(prefix="distrohop-backup-") as temporary:
        staging = Path(temporary)
        payload = staging / "payload"
        payload.mkdir(mode=0o700)
        for browser, profile, relative in _profile_destinations(selected_profiles):
            source = Path(str(profile["path"]))
            destination = payload / relative
            callback(Event("step", "Capturando perfil", {"source": str(source)}))
            if browser.get("engine") == "chromium":
                summary = chromium_linux.capture_profile(
                    source, destination, browser_id=str(browser["id"])
                )
            elif browser.get("engine") == "firefox":
                summary = firefox.capture_profile(source, destination)
            else:
                callback(
                    Event(
                        "warn",
                        "Engine desconhecida; perfil pulado",
                        {"engine": browser.get("engine"), "source": str(source)},
                    )
                )
                continue
            for warning in summary["warnings"]:
                capture_warnings.append("{}: {}".format(source, warning))
                callback(Event("warn", warning, {"source": str(source)}))
            captured_browsers.append(
                {
                    "id": browser["id"],
                    "name": browser.get("name"),
                    "engine": browser.get("engine"),
                    "version": browser.get("version"),
                    "packaging": browser.get("packaging"),
                    "profile": profile.get("name"),
                    "bundle_path": str(relative),
                    "captured": summary,
                }
            )
        callback(Event("step", "Capturando contas de IA"))
        captured_ai, ai_warnings = extras.capture_ai_accounts(
            selected_accounts, payload / "ai"
        )
        for warning in ai_warnings:
            capture_warnings.append(warning)
            callback(Event("warn", warning))
        callback(Event("step", "Capturando dados do sistema"))
        captured_extras = extras.capture_extras(
            plan.home,
            plan.selection.extras,
            payload / "system",
            manager=str(plan.inventory.get("os", {}).get("manager") or ""),
        )
        for warning in captured_extras["warnings"]:
            capture_warnings.append(warning)
            callback(Event("warn", warning))
        metadata = {
            "format_version": 1,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "source": {
                "platform": "linux",
                "distro": dict(plan.inventory.get("os", {})),
                "hostname": socket.gethostname(),
            },
            "browsers": captured_browsers,
            "captured": {
                "ai_accounts": captured_ai,
                "extras": captured_extras["captured"],
                "packages": captured_extras["packages"],
            },
            "warnings": capture_warnings,
        }
        canonical = staging / plan.bundle_name
        callback(Event("step", "Montando bundle", {"encrypted": plan.encrypted}))
        manifest = assemble_bundle(
            payload,
            canonical,
            metadata=metadata,
            encrypted=plan.encrypted,
            password=password,
        )
        callback(Event("step", "Gravando e verificando destinos"))
        destinations = publish_to_targets(
            canonical,
            plan.targets,
            plan.bundle_name,
            require_private_permissions=not plan.encrypted,
        )
        for destination in destinations:
            if not verify_bundle(destination):
                raise RuntimeError("bundle publicado falhou na verificação: {}".format(destination))
        result = {
            "bundle": plan.bundle_name,
            "destinations": [str(path) for path in destinations],
            "manifest_sha256": [
                sha256_file(path / "manifest.json") for path in destinations
            ],
            "encrypted": manifest["encrypted"],
            "warnings": capture_warnings,
        }
    callback(Event("done", "Backup concluído e verificado", result))
    return result


def _safe_bundle_prefix(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("caminho inseguro no manifesto: {}".format(value))
    return str(path)


def _select_restore_component(
    manifest: Mapping[str, Any],
    browser_id: Optional[str],
    source_profile: Optional[str],
) -> Mapping[str, Any]:
    components = [
        item for item in manifest.get("browsers", []) if isinstance(item, dict)
    ]
    if browser_id:
        components = [item for item in components if item.get("id") == browser_id]
    if source_profile:
        components = [
            item
            for item in components
            if source_profile in (
                str(item.get("profile") or ""),
                str(item.get("bundle_path") or ""),
            )
        ]
    if not components:
        raise ValueError("nenhum perfil do bundle corresponde à seleção")
    if len(components) != 1:
        choices = ", ".join(
            "{}/{}".format(item.get("id"), item.get("profile"))
            for item in components
        )
        raise ValueError(
            "selecione um perfil de origem com --browser e --source-profile: {}".format(
                choices
            )
        )
    return components[0]


def _component_prefix(component: Mapping[str, Any]) -> str:
    explicit = component.get("bundle_path")
    if explicit:
        return _safe_bundle_prefix(str(explicit))
    browser = _safe_name(str(component.get("id") or "browser"))
    profile = _safe_name(str(component.get("profile") or "profile"))
    return "browsers/{}/{}".format(browser, profile)


def _target_profile_for(
    inventory: Mapping[str, Any],
    component: Mapping[str, Any],
    explicit: Optional[Path],
    target_browser_id: Optional[str] = None,
) -> Path:
    if explicit is not None:
        return Path(explicit)
    browsers_found = [
        item
        for item in inventory.get("browsers", [])
        if item.get("id") == (target_browser_id or component.get("id"))
    ]
    profiles = [
        profile
        for browser in browsers_found
        for profile in browser.get("profiles", [])
    ]
    source_name = str(component.get("profile") or "")
    same_name = [profile for profile in profiles if str(profile.get("name")) == source_name]
    if len(same_name) == 1:
        return Path(str(same_name[0]["path"]))
    if len(profiles) == 1:
        return Path(str(profiles[0]["path"]))
    if not profiles:
        raise ValueError(
            "perfil de destino não detectado; informe --target-profile"
        )
    choices = ", ".join(str(profile["path"]) for profile in profiles)
    raise ValueError(
        "há vários perfis de destino; informe --target-profile: {}".format(choices)
    )


def _engine_for_browser(
    browser_id: str,
    inventory: Mapping[str, Any],
    *,
    fallback: Optional[str] = None,
) -> str:
    detected = {
        str(item.get("engine") or "")
        for item in inventory.get("browsers", [])
        if item.get("id") == browser_id and item.get("engine")
    }
    if len(detected) == 1:
        return detected.pop()
    for definition in browsers.load_definitions():
        if definition.get("id") == browser_id:
            return str(definition.get("engine") or "")
    if fallback:
        return fallback
    raise ValueError(
        "engine do navegador de destino {} não é conhecida".format(browser_id)
    )


def plan_restore(
    bundle: Path,
    *,
    browser_id: Optional[str] = None,
    source_profile: Optional[str] = None,
    target_browser_id: Optional[str] = None,
    target_profile: Optional[Path] = None,
    install: bool = False,
    inventory: Optional[Mapping[str, Any]] = None,
    callback: EventCallback = discard_event,
) -> RestorePlan:
    data = inventory or list_inventory(callback=callback)
    if data.get("platform") != "linux":
        raise RuntimeError("a Fase 3 implementa restore somente no Linux")
    bundle = Path(bundle)
    manifest = read_manifest(bundle)
    if not verify_bundle(bundle):
        raise ValueError("checksums do bundle não conferem")
    component = _select_restore_component(manifest, browser_id, source_profile)
    prefix = _component_prefix(component)
    source_engine = str(component.get("engine") or "")
    target_id = str(target_browser_id or component.get("id") or "")
    target_engine = _engine_for_browser(
        target_id,
        data,
        fallback=source_engine if target_id == component.get("id") else None,
    )
    mode = "raw" if source_engine == target_engine else "neutral"
    payload_prefix = prefix + ("/raw" if mode == "raw" else "/neutral")
    entries = manifest["files"]
    selected_files = sorted(
        relative
        for relative in entries
        if relative.startswith(payload_prefix + "/")
    )
    if not selected_files:
        raise ValueError(
            "o perfil selecionado não contém dados {}".format(
                "raw" if mode == "raw" else "neutros"
            )
        )
    target = _target_profile_for(
        data,
        component,
        target_profile,
        target_browser_id=target_id,
    )
    matching_browsers = [
        item
        for item in data.get("browsers", [])
        if item.get("id") == target_id
    ]
    installed = any(item.get("installed") is not False for item in matching_browsers)
    install_command: Tuple[str, ...] = ()
    if not installed:
        if not install:
            raise ValueError(
                "{} não está instalado; repita com --install".format(
                    target_id
                )
            )
        install_command = installer.plan_install(
            target_id, data.get("os", {})
        )
    if mode == "raw":
        outputs = tuple(
            str(target / PurePosixPath(relative).relative_to(payload_prefix))
            for relative in selected_files
        )
        warnings: Tuple[str, ...] = ()
    elif target_engine == "firefox":
        outputs = (
            str(target / "cookies.sqlite"),
            str(target / "places.sqlite"),
            str(target / "distrohop-logins.csv"),
        )
        warnings = (
            "Senhas não são importadas automaticamente entre engines.",
            "Alguns sites podem exigir novo login por dados presos ao localStorage.",
        )
    else:
        outputs = (
            str(target / "Network" / "Cookies"),
            str(target / "Bookmarks"),
            str(target / "distrohop-logins.csv"),
        )
        warnings = (
            "Senhas não são importadas automaticamente entre engines.",
            "Alguns sites podem exigir novo login por dados presos ao localStorage.",
        )
    outputs += ("{}.distrohop-before-<data>".format(target),)
    callback(
        Event(
            "plan",
            "Plano de restauração criado",
            {
                "browser": component.get("id"),
                "target_browser": target_id,
                "mode": mode,
                "files": len(selected_files),
            },
        )
    )
    return RestorePlan(
        bundle=bundle,
        component=component,
        target_profile=target,
        raw_prefix=payload_prefix,
        sources=tuple(str(bundle / relative) for relative in selected_files),
        outputs=outputs,
        encrypted=bool(manifest.get("encrypted")),
        target_browser_id=target_id,
        target_engine=target_engine,
        mode=mode,
        warnings=warnings,
        install_command=install_command,
    )


def run_restore(
    plan: RestorePlan,
    *,
    password: Optional[str] = None,
    callback: EventCallback = discard_event,
    running_check=is_browser_running,
) -> Dict[str, Any]:
    browser_id = plan.target_browser_id or str(plan.component.get("id") or "")
    if running_check(browser_id):
        raise RuntimeError(
            "{} está aberto; feche todas as janelas antes do restore".format(
                plan.component.get("name") or browser_id
            )
        )
    if plan.install_command:
        callback(
            Event(
                "step",
                "Instalando navegador",
                {"command": list(plan.install_command)},
            )
        )
        installer.run_install(plan.install_command)
    callback(Event("started", "Verificando bundle", {"bundle": str(plan.bundle)}))
    with materialize_payload(plan.bundle, password=password) as payload:
        if not verify_materialized_payload(plan.bundle, payload):
            raise ValueError("checksums do payload decriptado não conferem")
        source = payload / PurePosixPath(plan.raw_prefix)
        if plan.mode == "raw":
            callback(
                Event(
                    "step",
                    "Aplicando perfil raw",
                    {"source": str(source), "target": str(plan.target_profile)},
                )
            )
            result = apply_raw_profile(source, plan.target_profile)
        else:
            callback(
                Event(
                    "step",
                    "Convertendo perfil entre engines",
                    {
                        "source": str(source),
                        "target": str(plan.target_profile),
                        "target_engine": plan.target_engine,
                    },
                )
            )
            result = apply_neutral_profile(
                source,
                plan.target_profile,
                source_engine=str(plan.component.get("engine") or ""),
                target_engine=plan.target_engine,
            )
            for warning in result.get("warnings", ()):
                callback(Event("warn", str(warning)))
    result.update({"browser": browser_id, "mode": plan.mode})
    callback(Event("done", "Restore concluído", result))
    return result
