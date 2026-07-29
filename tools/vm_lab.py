#!/usr/bin/env python3
"""Low-impact, headless QEMU lab for cross-distro Distrohop smoke tests.

The lab deliberately avoids libvirt and desktop frontends.  It permits one VM
at a time, binds SSH to localhost, uses disposable qcow2 overlays, and refuses
to start when the host cannot spare the configured guest RAM plus a safety
margin.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlparse


REPO_ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = REPO_ROOT / "tools" / "vm" / "matrix.json"
MAX_MEMORY_MB = 2048
MAX_CPUS = 2
MIN_HOST_MARGIN_MB = 1024
SSH_USER = "distrohop"


class LabError(RuntimeError):
    """An actionable lab failure."""


def load_matrix(path: Path = MATRIX_PATH) -> Dict[str, Any]:
    try:
        matrix = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as error:
        raise LabError("invalid VM matrix {}: {}".format(path, error)) from error
    validate_matrix(matrix)
    return matrix


def validate_matrix(matrix: Mapping[str, Any]) -> None:
    defaults = matrix.get("defaults")
    distros = matrix.get("distros")
    if matrix.get("schema_version") != 1 or not isinstance(defaults, dict):
        raise LabError("VM matrix has an unsupported schema")
    if not isinstance(distros, list) or not distros:
        raise LabError("VM matrix must contain distributions")
    memory = int(defaults.get("memory_mb", 0))
    cpus = int(defaults.get("cpus", 0))
    if not 256 <= memory <= MAX_MEMORY_MB:
        raise LabError("VM memory must be between 256 and {} MiB".format(MAX_MEMORY_MB))
    if not 1 <= cpus <= MAX_CPUS:
        raise LabError("VM CPUs must be between 1 and {}".format(MAX_CPUS))
    identifiers = set()
    ports = set()
    for entry in distros:
        if not isinstance(entry, dict):
            raise LabError("every distro entry must be an object")
        identifier = str(entry.get("id", ""))
        port = int(entry.get("ssh_port", 0))
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", identifier):
            raise LabError("unsafe distro id: {!r}".format(identifier))
        if identifier in identifiers or port in ports:
            raise LabError("distro ids and SSH ports must be unique")
        identifiers.add(identifier)
        ports.add(port)
        if not 1024 <= port <= 65535:
            raise LabError("invalid SSH port for {}".format(identifier))
        if entry.get("automated"):
            for key in ("image_url", "checksum_url"):
                value = str(entry.get(key, ""))
                if not value.startswith("https://"):
                    raise LabError("{} requires an HTTPS {}".format(identifier, key))
            if entry.get("checksum_algorithm") not in hashlib.algorithms_available:
                raise LabError("unsupported checksum for {}".format(identifier))
            commands = entry.get("setup_commands")
            if not isinstance(commands, list) or not commands:
                raise LabError("{} has no setup commands".format(identifier))


def state_root(environ: Optional[Mapping[str, str]] = None) -> Path:
    env = os.environ if environ is None else environ
    override = env.get("DISTROHOP_VM_HOME")
    if override:
        return Path(override).expanduser().resolve()
    data_home = Path(env.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return (data_home / "distrohop" / "vm-lab").resolve()


def distro_entry(matrix: Mapping[str, Any], identifier: str) -> Dict[str, Any]:
    for entry in matrix["distros"]:
        if entry["id"] == identifier:
            return dict(entry)
    choices = ", ".join(item["id"] for item in matrix["distros"])
    raise LabError("unknown distro {!r}; choose one of: {}".format(identifier, choices))


def _paths(root: Path, distro: Mapping[str, Any]) -> Dict[str, Path]:
    identifier = str(distro["id"])
    image_name = Path(urlparse(str(distro.get("image_url") or "")).path).name
    suffix = Path(image_name).suffix if image_name else ".qcow2"
    instance = root / "instances" / identifier
    return {
        "root": root,
        "base": root / "base" / (identifier + suffix),
        "checksum": root / "base" / (identifier + ".checksums"),
        "instance": instance,
        "overlay": instance / "system.qcow2",
        "data": instance / "data.qcow2",
        "seed": instance / "seed.img",
        "user_data": instance / "user-data",
        "meta_data": instance / "meta-data",
        "vars": instance / "OVMF_VARS.fd",
        "pid": instance / "qemu.pid",
        "serial": instance / "serial.log",
        "qemu_log": instance / "qemu.log",
        "key": root / "keys" / "id_ed25519",
        "public_key": root / "keys" / "id_ed25519.pub",
        "reports": root / "reports",
    }


def _run(
    argv: Sequence[str],
    *,
    check: bool = True,
    capture_output: bool = False,
    text: bool = True,
    input_value: Optional[Any] = None,
) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            list(argv),
            check=check,
            capture_output=capture_output,
            text=text,
            input=input_value,
        )
    except FileNotFoundError as error:
        raise LabError("required command not found: {}".format(argv[0])) from error
    except subprocess.CalledProcessError as error:
        detail = ""
        if error.stderr:
            detail = ": " + str(error.stderr).strip()
        raise LabError("command failed ({}{})".format(shlex.join(argv), detail)) from error


def parse_checksum(text: str, filename: str, algorithm: str) -> str:
    length = hashlib.new(algorithm).digest_size * 2
    digest_pattern = r"([0-9a-fA-F]{{{}}})".format(length)
    escaped = re.escape(filename)
    patterns = (
        r"(?im)^\s*{}\s+\*?{}\s*$".format(digest_pattern, escaped),
        r"(?im)^\s*{}\s*\(\s*{}\s*\)\s*=\s*{}\s*$".format(
            re.escape(algorithm.upper()), escaped, digest_pattern
        ),
        r"(?im)^\s*{}\s*$".format(digest_pattern),
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).casefold()
    raise LabError("checksum for {} was not found in the signed/index file".format(filename))


def hash_file(path: Path, algorithm: str, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _ensure_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass


def _ensure_key(paths: Mapping[str, Path]) -> None:
    if paths["key"].is_file() and paths["public_key"].is_file():
        return
    _ensure_private_dir(paths["key"].parent)
    if paths["key"].exists() or paths["public_key"].exists():
        raise LabError("incomplete SSH keypair at {}".format(paths["key"].parent))
    _run(
        (
            "ssh-keygen",
            "-q",
            "-t",
            "ed25519",
            "-N",
            "",
            "-C",
            "distrohop-vm-lab",
            "-f",
            str(paths["key"]),
        )
    )


def _firmware() -> Tuple[Path, Path]:
    candidates = (
        (
            Path("/usr/share/edk2/x64/OVMF_CODE.4m.fd"),
            Path("/usr/share/edk2/x64/OVMF_VARS.4m.fd"),
        ),
        (
            Path("/usr/share/OVMF/OVMF_CODE_4M.fd"),
            Path("/usr/share/OVMF/OVMF_VARS_4M.fd"),
        ),
        (
            Path("/usr/share/OVMF/OVMF_CODE.fd"),
            Path("/usr/share/OVMF/OVMF_VARS.fd"),
        ),
    )
    for code, variables in candidates:
        if code.is_file() and variables.is_file():
            return code, variables
    raise LabError("OVMF firmware was not found; install edk2-ovmf")


def available_memory_mb(path: Path = Path("/proc/meminfo")) -> int:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    raise LabError("could not read MemAvailable from /proc/meminfo")


def _pid(
    paths: Mapping[str, Path],
    proc_root: Path = Path("/proc"),
) -> Optional[int]:
    try:
        value = int(paths["pid"].read_text(encoding="ascii").strip())
        os.kill(value, 0)
    except (OSError, ValueError):
        return None
    try:
        command = proc_root / str(value) / "cmdline"
        arguments = command.read_bytes().split(b"\0")
        decoded = [item.decode("utf-8", errors="replace") for item in arguments if item]
    except OSError:
        return None
    if not decoded or "qemu-system" not in Path(decoded[0]).name:
        return None
    if str(paths["pid"]) not in decoded:
        return None
    return value


def running_instances(root: Path, matrix: Mapping[str, Any]) -> List[Tuple[str, int]]:
    running = []
    for distro in matrix["distros"]:
        paths = _paths(root, distro)
        pid = _pid(paths)
        if pid is not None:
            running.append((str(distro["id"]), pid))
    return running


def _require_core(distro: Mapping[str, Any]) -> None:
    if not distro.get("automated"):
        raise LabError(
            "{} is an extended/manual target: {}".format(
                distro["id"], distro.get("reason") or "not automated"
            )
        )


def command_list(matrix: Mapping[str, Any], root: Path) -> None:
    print("ID                       TIER       AUTOMATION  IMAGE  VM")
    for distro in matrix["distros"]:
        paths = _paths(root, distro)
        automation = "ready" if distro.get("automated") else "manual"
        image = "cached" if paths["base"].is_file() else "missing"
        status = "running" if _pid(paths) is not None else (
            "created" if paths["overlay"].is_file() else "absent"
        )
        print(
            "{:<24} {:<10} {:<11} {:<6} {}".format(
                distro["id"], distro["tier"], automation, image, status
            )
        )


def command_doctor(matrix: Mapping[str, Any], root: Path) -> bool:
    defaults = matrix["defaults"]
    checks = []
    checks.append(("Linux host", sys.platform.startswith("linux"), sys.platform))
    checks.append(("/dev/kvm access", os.access("/dev/kvm", os.R_OK | os.W_OK), "/dev/kvm"))
    for command in (
        "curl",
        "qemu-img",
        "qemu-system-x86_64",
        "cloud-localds",
        "ssh",
        "ssh-keygen",
        "git",
        "nice",
        "ionice",
    ):
        found = shutil.which(command)
        checks.append((command, bool(found), found or "not found"))
    try:
        code, variables = _firmware()
        checks.append(("OVMF firmware", True, "{} + {}".format(code, variables)))
    except LabError as error:
        checks.append(("OVMF firmware", False, str(error)))
    try:
        available = available_memory_mb()
        required = int(defaults["memory_mb"]) + MIN_HOST_MARGIN_MB
        checks.append(
            (
                "host memory gate",
                available >= required,
                "{} MiB available; {} MiB required to start".format(available, required),
            )
        )
    except LabError as error:
        checks.append(("host memory gate", False, str(error)))
    probe = root
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    writable = probe.is_dir() and os.access(probe, os.W_OK)
    checks.append(
        (
            "state directory",
            writable,
            "{} (checked writable ancestor {})".format(root, probe),
        )
    )
    for label, passed, detail in checks:
        print("[{}] {:<20} {}".format("ok" if passed else "FAIL", label, detail))
    print(
        "Policy: one VM at a time, {} MiB RAM, {} vCPU, localhost-only SSH.".format(
            defaults["memory_mb"], defaults["cpus"]
        )
    )
    return all(item[1] for item in checks)


def command_fetch(distro: Mapping[str, Any], root: Path) -> None:
    _require_core(distro)
    paths = _paths(root, distro)
    _ensure_private_dir(paths["base"].parent)
    image_name = Path(urlparse(str(distro["image_url"])).path).name
    partial = paths["base"].with_suffix(paths["base"].suffix + ".part")
    print("Fetching checksum index for {}...".format(distro["name"]))
    _run(
        (
            "curl",
            "--fail",
            "--location",
            "--retry",
            "3",
            "--silent",
            "--show-error",
            "--output",
            str(paths["checksum"]),
            str(distro["checksum_url"]),
        )
    )
    expected = parse_checksum(
        paths["checksum"].read_text(encoding="utf-8", errors="replace"),
        image_name,
        str(distro["checksum_algorithm"]),
    )
    if paths["base"].is_file():
        if hash_file(paths["base"], str(distro["checksum_algorithm"])) == expected:
            print("Verified cached image: {}".format(paths["base"]))
            return
        raise LabError("cached image checksum failed; move it aside before retrying")
    print("Fetching image at a host-friendly 4 MiB/s (resumable)...")
    _run(
        (
            "curl",
            "--fail",
            "--location",
            "--continue-at",
            "-",
            "--retry",
            "3",
            "--limit-rate",
            "4M",
            "--output",
            str(partial),
            str(distro["image_url"]),
        )
    )
    actual = hash_file(partial, str(distro["checksum_algorithm"]))
    if actual != expected:
        quarantine = partial.with_name(
            "{}.bad-{}".format(partial.name, int(time.time()))
        )
        os.replace(str(partial), str(quarantine))
        raise LabError(
            "downloaded image checksum failed (expected {}, got {}); "
            "the bad file was quarantined as {}".format(
                expected, actual, quarantine
            )
        )
    os.replace(str(partial), str(paths["base"]))
    paths["base"].chmod(0o600)
    print("Verified image: {}".format(paths["base"]))


def cloud_config(
    public_key: str,
    swap_mb: int,
    *,
    unlock_user: bool = False,
) -> str:
    key = " ".join(public_key.strip().split())
    if not key.startswith(("ssh-ed25519 ", "ssh-rsa ", "ecdsa-sha2-")):
        raise LabError("generated SSH public key has an unexpected format")
    return """#cloud-config
users:
  - name: {user}
    lock_passwd: {lock_passwd}
    shell: /bin/sh
    sudo: "ALL=(ALL) NOPASSWD:ALL"
    ssh_authorized_keys:
      - {key}
ssh_pwauth: false
disable_root: true
package_update: false
packages:
  - sudo
growpart:
  mode: auto
  devices: ['/']
resize_rootfs: true
runcmd:
  - [sh, -c, "fallocate -l {swap}M /swapfile || dd if=/dev/zero of=/swapfile bs=1M count={swap}"]
  - [chmod, '0600', /swapfile]
  - [mkswap, /swapfile]
  - [swapon, /swapfile]
  - [sh, -c, "grep -q '^/swapfile ' /etc/fstab || printf '/swapfile none swap sw 0 0\\n' >> /etc/fstab"]
final_message: "Distrohop VM ready"
""".format(
        user=SSH_USER,
        key=key,
        lock_passwd="false" if unlock_user else "true",
        swap=int(swap_mb),
    )


def _base_format(path: Path) -> str:
    result = _run(
        ("qemu-img", "info", "--output=json", str(path)),
        capture_output=True,
    )
    try:
        value = json.loads(result.stdout)["format"]
    except (ValueError, KeyError, TypeError) as error:
        raise LabError("qemu-img could not identify {}".format(path)) from error
    if value not in ("qcow2", "raw"):
        raise LabError("unsupported base image format: {}".format(value))
    return str(value)


def command_create(
    distro: Mapping[str, Any],
    defaults: Mapping[str, Any],
    root: Path,
) -> None:
    _require_core(distro)
    paths = _paths(root, distro)
    if not paths["base"].is_file():
        raise LabError("base image missing; run fetch {}".format(distro["id"]))
    if paths["instance"].exists():
        raise LabError(
            "instance already exists at {}; it was not overwritten".format(paths["instance"])
        )
    _ensure_key(paths)
    _ensure_private_dir(paths["instance"].parent)
    staging = Path(
        tempfile.mkdtemp(
            prefix=".{}-create-".format(distro["id"]),
            dir=str(paths["instance"].parent),
        )
    )
    staged = dict(paths)
    staged["instance"] = staging
    for key in (
        "overlay",
        "data",
        "seed",
        "user_data",
        "meta_data",
        "vars",
        "pid",
        "serial",
        "qemu_log",
    ):
        staged[key] = staging / paths[key].name
    try:
        public_key = paths["public_key"].read_text(encoding="ascii")
        staged["user_data"].write_text(
            cloud_config(
                public_key,
                int(defaults["guest_swap_mb"]),
                unlock_user=bool(distro.get("unlock_user", False)),
            ),
            encoding="utf-8",
        )
        staged["meta_data"].write_text(
            "instance-id: distrohop-{}\nlocal-hostname: dh-{}\n".format(
                distro["id"], distro["id"]
            ),
            encoding="utf-8",
        )
        for private_file in (staged["user_data"], staged["meta_data"]):
            private_file.chmod(0o600)
        _run(
            (
                "cloud-localds",
                str(staged["seed"]),
                str(staged["user_data"]),
                str(staged["meta_data"]),
            )
        )
        staged["seed"].chmod(0o600)
        backing_format = _base_format(paths["base"])
        _run(
            (
                "qemu-img",
                "create",
                "-f",
                "qcow2",
                "-F",
                backing_format,
                "-b",
                str(paths["base"].resolve()),
                str(staged["overlay"]),
            )
        )
        _run(
            (
                "qemu-img",
                "resize",
                str(staged["overlay"]),
                "{}G".format(int(defaults["system_disk_gib"])),
            )
        )
        _run(
            (
                "qemu-img",
                "create",
                "-f",
                "qcow2",
                str(staged["data"]),
                "{}G".format(int(defaults["data_disk_gib"])),
            )
        )
        _code, variables = _firmware()
        shutil.copyfile(str(variables), str(staged["vars"]))
        staged["vars"].chmod(0o600)
        os.replace(str(staging), str(paths["instance"]))
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    print("Created disposable instance: {}".format(paths["instance"]))


def qemu_command(
    distro: Mapping[str, Any],
    defaults: Mapping[str, Any],
    root: Path,
) -> List[str]:
    paths = _paths(root, distro)
    code, _variables = _firmware()
    memory = int(defaults["memory_mb"])
    cpus = int(defaults["cpus"])
    if memory > MAX_MEMORY_MB or cpus > MAX_CPUS:
        raise LabError("resource policy exceeds the hard lab limit")
    return [
        "nice",
        "-n",
        "15",
        "ionice",
        "-c",
        "3",
        "qemu-system-x86_64",
        "-name",
        "distrohop-{}".format(distro["id"]),
        "-machine",
        "q35,accel=kvm",
        "-cpu",
        "host",
        "-m",
        str(memory),
        "-smp",
        str(cpus),
        "-display",
        "none",
        "-daemonize",
        "-pidfile",
        str(paths["pid"]),
        "-D",
        str(paths["qemu_log"]),
        "-serial",
        "file:{}".format(paths["serial"]),
        "-drive",
        "if=pflash,format=raw,readonly=on,file={}".format(code),
        "-drive",
        "if=pflash,format=raw,file={}".format(paths["vars"]),
        "-drive",
        "if=virtio,format=qcow2,file={}".format(paths["overlay"]),
        "-drive",
        "if=virtio,format=qcow2,file={}".format(paths["data"]),
        "-drive",
        "if=virtio,format=raw,readonly=on,file={}".format(paths["seed"]),
        "-device",
        "virtio-rng-pci",
        "-netdev",
        "user,id=net0,hostfwd=tcp:127.0.0.1:{}-:22".format(distro["ssh_port"]),
        "-device",
        "virtio-net-pci,netdev=net0",
    ]


def command_start(
    distro: Mapping[str, Any],
    defaults: Mapping[str, Any],
    root: Path,
) -> None:
    _require_core(distro)
    paths = _paths(root, distro)
    required_files = ("overlay", "data", "seed", "vars", "key")
    missing = [name for name in required_files if not paths[name].is_file()]
    if missing:
        raise LabError(
            "instance is incomplete (missing {}); run create {}".format(
                ", ".join(missing), distro["id"]
            )
        )
    running = running_instances(root, {"distros": [distro]})
    if running:
        raise LabError("{} is already running as PID {}".format(*running[0]))
    others = running_instances(root, load_matrix())
    if others:
        raise LabError(
            "one-VM policy: stop {} (PID {}) first".format(others[0][0], others[0][1])
        )
    required_memory = int(defaults["memory_mb"]) + MIN_HOST_MARGIN_MB
    available = available_memory_mb()
    if available < required_memory:
        raise LabError(
            "start refused: {} MiB available, {} MiB required (guest plus margin)".format(
                available, required_memory
            )
        )
    _run(qemu_command(distro, defaults, root))
    pid = _pid(paths)
    if pid is None:
        raise LabError("QEMU exited during startup; inspect {}".format(paths["qemu_log"]))
    print(
        "Started {} as PID {}: {} MiB RAM, {} vCPU, SSH 127.0.0.1:{}".format(
            distro["id"],
            pid,
            defaults["memory_mb"],
            defaults["cpus"],
            distro["ssh_port"],
        )
    )


def ssh_argv(distro: Mapping[str, Any], root: Path) -> List[str]:
    paths = _paths(root, distro)
    return [
        "ssh",
        "-i",
        str(paths["key"]),
        "-p",
        str(distro["ssh_port"]),
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "UserKnownHostsFile={}".format(root / "known_hosts"),
        "-o",
        "ConnectTimeout=5",
        "{}@127.0.0.1".format(SSH_USER),
    ]


def _ssh(
    distro: Mapping[str, Any],
    root: Path,
    remote_command: str,
    *,
    check: bool = True,
    capture_output: bool = False,
    input_value: Optional[Any] = None,
    text: bool = True,
) -> subprocess.CompletedProcess:
    return _run(
        [*ssh_argv(distro, root), remote_command],
        check=check,
        capture_output=capture_output,
        input_value=input_value,
        text=text,
    )


def command_wait(distro: Mapping[str, Any], root: Path, timeout: int) -> None:
    paths = _paths(root, distro)
    if _pid(paths) is None:
        raise LabError("{} is not running".format(distro["id"]))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = _ssh(
            distro,
            root,
            "cloud-init status --wait >/dev/null 2>&1 || true; printf ready",
            check=False,
            capture_output=True,
        )
        if result.returncode == 0 and result.stdout.strip() == "ready":
            print("{} is ready for SSH".format(distro["id"]))
            return
        if _pid(paths) is None:
            raise LabError("VM stopped while waiting; inspect {}".format(paths["serial"]))
        time.sleep(3)
    raise LabError(
        "SSH did not become ready in {}s; inspect {}".format(timeout, paths["serial"])
    )


def command_setup(distro: Mapping[str, Any], root: Path) -> None:
    _require_core(distro)
    for index, command in enumerate(distro["setup_commands"], 1):
        print("[{}/{}] {}".format(index, len(distro["setup_commands"]), command))
        _ssh(distro, root, "set -eu; {}".format(command))
    _ssh(distro, root, "touch ~/.distrohop-lab-setup")
    print("Guest dependencies installed for {}".format(distro["id"]))


def command_sync(distro: Mapping[str, Any], root: Path) -> None:
    if not (REPO_ROOT / ".git").exists():
        raise LabError("repository root does not contain .git")
    revision = _run(
        ("git", "rev-parse", "--verify", "HEAD"),
        capture_output=True,
    ).stdout.strip()
    archive = _run(
        ("git", "archive", "--format=tar", "HEAD"),
        capture_output=True,
        text=False,
    ).stdout
    _ssh(
        distro,
        root,
        "rm -rf ~/distrohop.new && mkdir -p ~/distrohop.new && "
        "tar -xf - -C ~/distrohop.new && "
        "rm -rf ~/distrohop.old && "
        "if [ -d ~/distrohop ]; then mv ~/distrohop ~/distrohop.old; fi && "
        "mv ~/distrohop.new ~/distrohop",
        input_value=archive,
        text=False,
    )
    print("Synced committed revision {} to {}".format(revision[:12], distro["id"]))


def command_test(distro: Mapping[str, Any], root: Path) -> None:
    paths = _paths(root, distro)
    remote = (
        "cd ~/distrohop && "
        "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin "
        "DISTROHOP_VM_DISTRO={} python3 tools/vm/guest_smoke.py"
    ).format(shlex.quote(str(distro["id"])))
    _ssh(distro, root, remote)
    _ensure_private_dir(paths["reports"])
    report = _ssh(
        distro,
        root,
        "cat ~/distrohop-vm-report.json",
        capture_output=True,
    ).stdout
    try:
        parsed = json.loads(report)
    except ValueError as error:
        raise LabError("guest returned an invalid JSON report") from error
    if parsed.get("ok") is not True or parsed.get("distro") != distro["id"]:
        raise LabError("guest report did not confirm a successful {} run".format(distro["id"]))
    target = paths["reports"] / "{}.json".format(distro["id"])
    target.write_text(json.dumps(parsed, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    target.chmod(0o600)
    print("Guest report: {}".format(target))


def command_stop(distro: Mapping[str, Any], root: Path) -> None:
    paths = _paths(root, distro)
    pid = _pid(paths)
    if pid is None:
        print("{} is already stopped".format(distro["id"]))
        return
    _ssh(distro, root, "sudo poweroff", check=False)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if _pid(paths) is None:
            print("Stopped {}".format(distro["id"]))
            return
        time.sleep(1)
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if _pid(paths) is None:
            print("Stopped {} after QEMU SIGTERM".format(distro["id"]))
            return
        time.sleep(1)
    raise LabError("VM did not stop; PID {} was left untouched".format(pid))


def command_destroy(distro: Mapping[str, Any], root: Path, confirmed: bool) -> None:
    paths = _paths(root, distro)
    if not confirmed:
        raise LabError("destroy requires --yes; no files were removed")
    if _pid(paths) is not None:
        raise LabError("{} is running; stop it before destroy".format(distro["id"]))
    expected_parent = (root / "instances").resolve()
    instance = paths["instance"].resolve()
    if instance.parent != expected_parent or instance.name != distro["id"]:
        raise LabError("refusing unsafe instance path {}".format(instance))
    if not instance.exists():
        print("{} has no instance to destroy".format(distro["id"]))
        return
    shutil.rmtree(instance)
    known_hosts = root / "known_hosts"
    if known_hosts.is_file():
        _run(
            (
                "ssh-keygen",
                "-q",
                "-R",
                "[127.0.0.1]:{}".format(distro["ssh_port"]),
                "-f",
                str(known_hosts),
            ),
            check=False,
        )
    print(
        "Destroyed disposable instance {}. The verified base image was preserved.".format(
            instance
        )
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Headless cross-distro Distrohop VM lab (2 GiB hard limit)"
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        help="state directory (default: DISTROHOP_VM_HOME or XDG data)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list", help="show matrix, image cache and VM state")
    subparsers.add_parser("doctor", help="perform read-only host checks")
    for name, help_text in (
        ("fetch", "download and verify one official cloud image"),
        ("create", "create a disposable overlay, seed and virtual data disk"),
        ("start", "start one low-priority headless VM"),
        ("setup", "install distro-specific guest dependencies"),
        ("sync", "copy the committed repository revision into the guest"),
        ("test", "run the guest smoke suite and retrieve its JSON report"),
        ("stop", "request guest shutdown, then stop only its exact QEMU PID"),
    ):
        command = subparsers.add_parser(name, help=help_text)
        command.add_argument("distro")
    wait = subparsers.add_parser("wait", help="wait until cloud-init and SSH are ready")
    wait.add_argument("distro")
    wait.add_argument("--timeout", type=int, default=300)
    destroy = subparsers.add_parser(
        "destroy",
        help="delete one stopped disposable instance while preserving its base image",
    )
    destroy.add_argument("distro")
    destroy.add_argument("--yes", action="store_true", help="confirm exact instance deletion")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    matrix = load_matrix()
    root = (args.state_dir.expanduser().resolve() if args.state_dir else state_root())
    try:
        if args.command == "list":
            command_list(matrix, root)
            return 0
        if args.command == "doctor":
            return 0 if command_doctor(matrix, root) else 1
        distro = distro_entry(matrix, args.distro)
        defaults = matrix["defaults"]
        if args.command == "fetch":
            command_fetch(distro, root)
        elif args.command == "create":
            command_create(distro, defaults, root)
        elif args.command == "start":
            command_start(distro, defaults, root)
        elif args.command == "wait":
            command_wait(distro, root, max(1, args.timeout))
        elif args.command == "setup":
            command_setup(distro, root)
        elif args.command == "sync":
            command_sync(distro, root)
        elif args.command == "test":
            command_test(distro, root)
        elif args.command == "stop":
            command_stop(distro, root)
        elif args.command == "destroy":
            command_destroy(distro, root, args.yes)
        return 0
    except LabError as error:
        print("vm-lab: {}".format(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
