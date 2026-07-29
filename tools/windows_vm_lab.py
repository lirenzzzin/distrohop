#!/usr/bin/env python3
"""Disposable, headless Windows 11 VM lab for Distrohop."""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import os
import secrets
import shlex
import shutil
import signal
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
ISO_URL = "https://aka.ms/Win11E-ISO-25H2-pt-br"
ISO_NAME = "windows11-enterprise-25h2-ptbr.iso"
ISO_SHA256 = "a5d6a86a9553bb730d1b723233108e90c1b9499f7284137218865919d4189ddd"
VM_NAME = "distrohop-windows-11"
SSH_USER = "distrohop"
SSH_PORT = 22410
MEMORY_MB = 4096
CPUS = 2
DISK_GIB = 64


class LabError(RuntimeError):
    """A safe, actionable lab failure."""


def state_root(environ: Optional[Mapping[str, str]] = None) -> Path:
    env = os.environ if environ is None else environ
    override = env.get("DISTROHOP_WINDOWS_VM_HOME")
    if override:
        return Path(override).expanduser().resolve()
    data = env.get("XDG_DATA_HOME")
    base = Path(data).expanduser() if data else Path.home() / ".local" / "share"
    return (base / "distrohop" / "windows-lab").resolve()


def paths(root: Path) -> Dict[str, Path]:
    instance = root / "instance"
    return {
        "root": root,
        "base": root / "base",
        "iso": root / "base" / ISO_NAME,
        "instance": instance,
        "disk": instance / "windows.qcow2",
        "answer": instance / "answer.img",
        "vars": instance / "OVMF_VARS.fd",
        "pid": instance / "qemu.pid",
        "qemu_log": instance / "qemu.log",
        "monitor": instance / "monitor.sock",
        "key": root / "keys" / "id_ed25519",
        "public_key": root / "keys" / "id_ed25519.pub",
        "known_hosts": root / "known_hosts",
        "reports": root / "reports",
    }


def _run(
    argv: Sequence[str],
    *,
    check: bool = True,
    capture: bool = False,
    input_value: Optional[bytes] = None,
    text: bool = True,
) -> subprocess.CompletedProcess:
    result = subprocess.run(
        list(argv),
        check=False,
        capture_output=capture,
        input=input_value,
        text=text,
    )
    if check and result.returncode:
        detail = ""
        if capture:
            raw = result.stderr or result.stdout or b""
            detail = raw if isinstance(raw, str) else raw.decode(errors="replace")
        raise LabError(
            "{} failed with exit {}{}".format(
                shlex.join(list(argv)),
                result.returncode,
                ": " + detail.strip()[-1200:] if detail.strip() else "",
            )
        )
    return result


def _private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_iso(path: Path) -> None:
    if not path.is_file():
        raise LabError("Windows ISO missing; run fetch first")
    actual = sha256_file(path)
    if actual.casefold() != ISO_SHA256:
        raise LabError(
            "Windows ISO checksum mismatch: expected {}, got {}".format(
                ISO_SHA256,
                actual,
            )
        )


def command_fetch(root: Path) -> None:
    selected = paths(root)
    _private_directory(selected["base"])
    if selected["iso"].is_file():
        verify_iso(selected["iso"])
        print("Verified cached Windows ISO: {}".format(selected["iso"]))
        return
    partial = selected["iso"].with_suffix(".iso.partial")
    _run(
        (
            "nice",
            "-n",
            "15",
            "ionice",
            "-c",
            "3",
            "curl",
            "-L",
            "--fail",
            "--retry",
            "3",
            "--continue-at",
            "-",
            "--limit-rate",
            "4M",
            "--output",
            str(partial),
            ISO_URL,
        )
    )
    if sha256_file(partial).casefold() != ISO_SHA256:
        raise LabError("downloaded Windows ISO failed the official SHA-256")
    partial.replace(selected["iso"])
    selected["iso"].chmod(0o600)
    print("Verified Windows ISO: {}".format(selected["iso"]))


def _firmware() -> tuple:
    code_candidates = (
        Path("/usr/share/edk2/x64/OVMF_CODE.4m.fd"),
        Path("/usr/share/OVMF/OVMF_CODE.fd"),
    )
    vars_candidates = (
        Path("/usr/share/edk2/x64/OVMF_VARS.4m.fd"),
        Path("/usr/share/OVMF/OVMF_VARS.fd"),
    )
    code = next((item for item in code_candidates if item.is_file()), None)
    variables = next((item for item in vars_candidates if item.is_file()), None)
    if code is None or variables is None:
        raise LabError("OVMF UEFI firmware was not found")
    return code, variables


def _ensure_key(selected: Mapping[str, Path]) -> None:
    if selected["key"].is_file() and selected["public_key"].is_file():
        return
    _private_directory(selected["key"].parent)
    _run(
        (
            "ssh-keygen",
            "-q",
            "-t",
            "ed25519",
            "-N",
            "",
            "-C",
            "distrohop-windows-lab",
            "-f",
            str(selected["key"]),
        )
    )
    selected["key"].chmod(0o600)


def setup_script(public_key: str) -> str:
    key = " ".join(public_key.strip().split())
    if not key.startswith("ssh-ed25519 "):
        raise LabError("Windows lab requires an Ed25519 public key")
    if "'" in key or "\r" in key or "\n" in key:
        raise LabError("unexpected character in generated public key")
    return r"""$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
powercfg.exe /change monitor-timeout-ac 0
powercfg.exe /change standby-timeout-ac 0

$capability = Get-WindowsCapability -Online |
  Where-Object Name -Like 'OpenSSH.Server*' |
  Select-Object -First 1
if (-not $capability) {
  throw 'OpenSSH Server capability was not found'
}
if ($capability.State -ne 'Installed') {
  Add-WindowsCapability -Online -Name $capability.Name | Out-Null
}

$sshRoot = Join-Path $env:ProgramData 'ssh'
New-Item -ItemType Directory -Path $sshRoot -Force | Out-Null
$keys = Join-Path $sshRoot 'administrators_authorized_keys'
Set-Content -LiteralPath $keys -Encoding ascii -Value '__PUBLIC_KEY__'
icacls.exe $keys /inheritance:r | Out-Null
icacls.exe $keys /grant '*S-1-5-18:F' '*S-1-5-32-544:F' | Out-Null

$config = Join-Path $sshRoot 'sshd_config'
if (-not (Select-String -LiteralPath $config -Pattern '^\s*PasswordAuthentication\s+no\s*$' -Quiet)) {
  Add-Content -LiteralPath $config -Encoding ascii -Value "`nPasswordAuthentication no"
}
if (-not (Select-String -LiteralPath $config -Pattern '^\s*PubkeyAuthentication\s+yes\s*$' -Quiet)) {
  Add-Content -LiteralPath $config -Encoding ascii -Value 'PubkeyAuthentication yes'
}

$shellKey = 'HKLM:\SOFTWARE\OpenSSH'
New-Item -Path $shellKey -Force | Out-Null
New-ItemProperty -Path $shellKey -Name DefaultShell -PropertyType String `
  -Value "$env:WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe" `
  -Force | Out-Null

Set-Service -Name sshd -StartupType Automatic
if (-not (Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue)) {
  New-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -DisplayName 'OpenSSH Server (sshd)' `
    -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 | Out-Null
}
$sshd = Get-Service -Name sshd
if ($sshd.Status -eq 'Running') { Restart-Service sshd } else { Start-Service sshd }
New-Item -ItemType Directory -Path 'C:\DistrohopLab' -Force | Out-Null
Set-Content -LiteralPath 'C:\DistrohopLab\ssh-ready.json' -Encoding utf8 `
  -Value '{"ssh":true,"password_authentication":false}'
""".replace("__PUBLIC_KEY__", key)


def autounattend(password: str) -> str:
    escaped = html.escape(password, quote=True)
    return r"""<?xml version="1.0" encoding="utf-8"?>
<unattend xmlns="urn:schemas-microsoft-com:unattend">
  <settings pass="windowsPE">
    <component name="Microsoft-Windows-International-Core-WinPE" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS" xmlns:wcm="http://schemas.microsoft.com/WMIConfig/2002/State">
      <SetupUILanguage><UILanguage>pt-BR</UILanguage></SetupUILanguage>
      <InputLocale>0416:00010416</InputLocale>
      <SystemLocale>pt-BR</SystemLocale>
      <UILanguage>pt-BR</UILanguage>
      <UserLocale>pt-BR</UserLocale>
    </component>
    <component name="Microsoft-Windows-Setup" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS" xmlns:wcm="http://schemas.microsoft.com/WMIConfig/2002/State">
      <RunSynchronous>
        <RunSynchronousCommand wcm:action="add">
          <Order>1</Order>
          <Path>reg add HKLM\SYSTEM\Setup\LabConfig /v BypassTPMCheck /t REG_DWORD /d 1 /f</Path>
        </RunSynchronousCommand>
        <RunSynchronousCommand wcm:action="add">
          <Order>2</Order>
          <Path>reg add HKLM\SYSTEM\Setup\LabConfig /v BypassSecureBootCheck /t REG_DWORD /d 1 /f</Path>
        </RunSynchronousCommand>
      </RunSynchronous>
      <DiskConfiguration>
        <Disk wcm:action="add">
          <DiskID>0</DiskID><WillWipeDisk>true</WillWipeDisk>
          <CreatePartitions>
            <CreatePartition wcm:action="add"><Order>1</Order><Type>EFI</Type><Size>260</Size></CreatePartition>
            <CreatePartition wcm:action="add"><Order>2</Order><Type>MSR</Type><Size>16</Size></CreatePartition>
            <CreatePartition wcm:action="add"><Order>3</Order><Type>Primary</Type><Extend>true</Extend></CreatePartition>
          </CreatePartitions>
          <ModifyPartitions>
            <ModifyPartition wcm:action="add"><Order>1</Order><PartitionID>1</PartitionID><Format>FAT32</Format><Label>System</Label></ModifyPartition>
            <ModifyPartition wcm:action="add"><Order>2</Order><PartitionID>3</PartitionID><Format>NTFS</Format><Label>Windows</Label><Letter>C</Letter></ModifyPartition>
          </ModifyPartitions>
        </Disk>
        <WillShowUI>OnError</WillShowUI>
      </DiskConfiguration>
      <ImageInstall>
        <OSImage>
          <InstallFrom><MetaData wcm:action="add"><Key>/IMAGE/INDEX</Key><Value>1</Value></MetaData></InstallFrom>
          <InstallTo><DiskID>0</DiskID><PartitionID>3</PartitionID></InstallTo>
          <WillShowUI>OnError</WillShowUI>
        </OSImage>
      </ImageInstall>
      <UserData>
        <AcceptEula>true</AcceptEula>
        <FullName>Distrohop</FullName>
        <Organization>Distrohop VM Lab</Organization>
      </UserData>
    </component>
  </settings>
  <settings pass="specialize">
    <component name="Microsoft-Windows-Shell-Setup" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS">
      <ComputerName>DH-WIN11</ComputerName>
      <TimeZone>E. South America Standard Time</TimeZone>
    </component>
  </settings>
  <settings pass="oobeSystem">
    <component name="Microsoft-Windows-International-Core" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS">
      <InputLocale>0416:00010416</InputLocale><SystemLocale>pt-BR</SystemLocale>
      <UILanguage>pt-BR</UILanguage><UserLocale>pt-BR</UserLocale>
    </component>
    <component name="Microsoft-Windows-Shell-Setup" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS">
      <OOBE>
        <HideEULAPage>true</HideEULAPage><HideLocalAccountScreen>true</HideLocalAccountScreen>
        <HideOnlineAccountScreens>true</HideOnlineAccountScreens><HideWirelessSetupInOOBE>true</HideWirelessSetupInOOBE>
        <NetworkLocation>Work</NetworkLocation><ProtectYourPC>3</ProtectYourPC>
        <SkipMachineOOBE>true</SkipMachineOOBE><SkipUserOOBE>true</SkipUserOOBE>
      </OOBE>
      <UserAccounts>
        <LocalAccounts>
          <LocalAccount wcm:action="add" xmlns:wcm="http://schemas.microsoft.com/WMIConfig/2002/State">
            <Name>distrohop</Name><DisplayName>Distrohop</DisplayName>
            <Group>Administradores</Group>
            <Password><Value>{password}</Value><PlainText>true</PlainText></Password>
          </LocalAccount>
        </LocalAccounts>
      </UserAccounts>
      <AutoLogon>
        <Enabled>true</Enabled><Username>distrohop</Username><Domain>.</Domain><LogonCount>4</LogonCount>
        <Password><Value>{password}</Value><PlainText>true</PlainText></Password>
      </AutoLogon>
      <FirstLogonCommands>
        <SynchronousCommand wcm:action="add" xmlns:wcm="http://schemas.microsoft.com/WMIConfig/2002/State">
          <Order>1</Order><Description>Prepare Distrohop SSH</Description>
          <CommandLine>cmd.exe /c if exist A:\setup.ps1 (powershell.exe -NoProfile -ExecutionPolicy Bypass -File A:\setup.ps1) else (exit /b 2)</CommandLine>
        </SynchronousCommand>
      </FirstLogonCommands>
    </component>
  </settings>
</unattend>
""".format(password=escaped)


def _answer_image(destination: Path, xml: str, powershell: str) -> None:
    with tempfile.TemporaryDirectory(prefix="distrohop-win-answer-") as item:
        root = Path(item)
        unattended = root / "Autounattend.xml"
        setup = root / "setup.ps1"
        unattended.write_text(xml, encoding="utf-8")
        setup.write_text(powershell, encoding="utf-8")
        _run(("truncate", "-s", "1440K", str(destination)))
        _run(("mkfs.vfat", "-n", "DISTROHOP", str(destination)))
        _run(("mcopy", "-i", str(destination), str(unattended), "::/Autounattend.xml"))
        _run(("mcopy", "-i", str(destination), str(setup), "::/setup.ps1"))
    destination.chmod(0o600)


def command_create(root: Path) -> None:
    selected = paths(root)
    verify_iso(selected["iso"])
    if selected["instance"].exists():
        raise LabError("Windows instance already exists; it was not overwritten")
    _ensure_key(selected)
    _private_directory(selected["instance"].parent)
    staging = Path(
        tempfile.mkdtemp(
            prefix=".windows-create-",
            dir=str(selected["instance"].parent),
        )
    )
    try:
        disk = staging / selected["disk"].name
        answer = staging / selected["answer"].name
        variables = staging / selected["vars"].name
        password = "Dh!" + secrets.token_urlsafe(24) + "9z"
        public_key = selected["public_key"].read_text(encoding="ascii")
        _answer_image(
            answer,
            autounattend(password),
            setup_script(public_key),
        )
        _run(("qemu-img", "create", "-f", "qcow2", str(disk), "{}G".format(DISK_GIB)))
        _code, template = _firmware()
        shutil.copyfile(str(template), str(variables))
        variables.chmod(0o600)
        os.replace(str(staging), str(selected["instance"]))
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    print("Created Windows instance with a {} GiB sparse disk".format(DISK_GIB))


def qemu_command(root: Path, *, installer: bool) -> List[str]:
    selected = paths(root)
    code, _variables = _firmware()
    command = [
        "nice",
        "-n",
        "15",
        "ionice",
        "-c",
        "3",
        "qemu-system-x86_64",
        "-name",
        VM_NAME,
        "-machine",
        "q35,accel=kvm",
        "-cpu",
        "host,hv_relaxed,hv_vapic,hv_spinlocks=0x1fff,hv_time",
        "-m",
        str(MEMORY_MB),
        "-smp",
        str(CPUS),
        "-display",
        "none",
        "-vga",
        "std",
        "-daemonize",
        "-pidfile",
        str(selected["pid"]),
        "-D",
        str(selected["qemu_log"]),
        "-monitor",
        "unix:{},server=on,wait=off".format(selected["monitor"]),
        "-drive",
        "if=pflash,format=raw,readonly=on,file={}".format(code),
        "-drive",
        "if=pflash,format=raw,file={}".format(selected["vars"]),
        "-drive",
        "file={},format=qcow2,if=ide,media=disk".format(selected["disk"]),
        "-device",
        "virtio-rng-pci",
        "-netdev",
        "user,id=net0,hostfwd=tcp:127.0.0.1:{}-:22".format(SSH_PORT),
        "-device",
        "e1000,netdev=net0",
        "-rtc",
        "base=localtime,clock=host",
    ]
    if installer:
        command.extend(
            (
                "-drive",
                "file={},media=cdrom,readonly=on".format(selected["iso"]),
                "-drive",
                "file={},format=raw,if=floppy,readonly=on".format(
                    selected["answer"]
                ),
                "-boot",
                "once=d,menu=off",
            )
        )
    return command


def _pid(selected: Mapping[str, Path], proc_root: Path = Path("/proc")) -> Optional[int]:
    try:
        pid = int(selected["pid"].read_text(encoding="ascii").strip())
        command = (proc_root / str(pid) / "cmdline").read_bytes().split(b"\0")
    except (OSError, ValueError):
        return None
    decoded = [item.decode(errors="replace") for item in command if item]
    joined = "\0".join(decoded)
    if (
        not decoded
        or "qemu-system-x86_64" not in Path(decoded[0]).name
        or VM_NAME not in joined
        or str(selected["disk"]) not in joined
        or str(selected["pid"]) not in joined
    ):
        return None
    return pid


def _other_distrohop_qemu() -> List[int]:
    current = []
    proc = Path("/proc")
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            command = (entry / "cmdline").read_bytes().decode(errors="replace")
        except OSError:
            continue
        if "qemu-system-x86_64" in command and "distrohop-" in command:
            current.append(int(entry.name))
    return current


def command_start(root: Path, *, installer: bool) -> None:
    selected = paths(root)
    if not selected["disk"].is_file() or not selected["vars"].is_file():
        raise LabError("Windows instance is missing; run create first")
    if _pid(selected) is not None:
        raise LabError("Windows VM is already running")
    others = _other_distrohop_qemu()
    if others:
        raise LabError("another Distrohop VM is running: {}".format(others))
    for stale in (selected["pid"], selected["monitor"]):
        try:
            stale.unlink()
        except FileNotFoundError:
            pass
    _run(qemu_command(root, installer=installer))
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        pid = _pid(selected)
        if pid is not None:
            print(
                "Started Windows as PID {}: {} MiB RAM, {} vCPU, no GPU passthrough".format(
                    pid,
                    MEMORY_MB,
                    CPUS,
                )
            )
            if installer:
                # The Microsoft ISO briefly asks for a key before booting.
                # Repeated space presses are harmless once Setup has started.
                for _attempt in range(3):
                    time.sleep(2)
                    _monitor(root, "sendkey spc")
            return
        time.sleep(0.25)
    raise LabError("QEMU exited during Windows startup")


def ssh_argv(root: Path) -> List[str]:
    selected = paths(root)
    return [
        "ssh",
        "-i",
        str(selected["key"]),
        "-p",
        str(SSH_PORT),
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "UserKnownHostsFile={}".format(selected["known_hosts"]),
        "-o",
        "ConnectTimeout=5",
        "{}@127.0.0.1".format(SSH_USER),
    ]


def _encoded_powershell(script: str) -> str:
    return base64.b64encode(script.encode("utf-16le")).decode("ascii")


def _ssh(
    root: Path,
    script: str,
    *,
    check: bool = True,
    capture: bool = False,
    input_value: Optional[bytes] = None,
    text: bool = True,
) -> subprocess.CompletedProcess:
    command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -EncodedCommand {}".format(
        _encoded_powershell(script)
    )
    return _run(
        [*ssh_argv(root), command],
        check=check,
        capture=capture,
        input_value=input_value,
        text=text,
    )


def command_wait(root: Path, timeout: int) -> None:
    selected = paths(root)
    if _pid(selected) is None:
        raise LabError("Windows VM is not running")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = _ssh(
            root,
            "if (Test-Path 'C:\\DistrohopLab\\ssh-ready.json') { 'ready' }",
            check=False,
            capture=True,
        )
        if result.returncode == 0 and result.stdout.strip() == "ready":
            print("Windows is ready for key-only SSH")
            return
        if _pid(selected) is None:
            raise LabError("Windows VM stopped during installation")
        time.sleep(10)
    raise LabError("Windows did not become ready within {} seconds".format(timeout))


def command_setup(root: Path) -> None:
    script = r"""$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$python = Get-ChildItem `
  "$env:LOCALAPPDATA\Programs\Python\Python*\python.exe", `
  "$env:ProgramFiles\Python*\python.exe" -ErrorAction SilentlyContinue |
  Sort-Object FullName -Descending | Select-Object -First 1 -ExpandProperty FullName
if (-not $python) {
  winget.exe install --id Python.Python.3.12 --exact --source winget --silent `
    --accept-package-agreements --accept-source-agreements
  if ($LASTEXITCODE -ne 0) { throw "WinGet Python install failed: $LASTEXITCODE" }
  $python = Get-ChildItem `
    "$env:LOCALAPPDATA\Programs\Python\Python*\python.exe", `
    "$env:ProgramFiles\Python*\python.exe" -ErrorAction SilentlyContinue |
    Sort-Object FullName -Descending | Select-Object -First 1 -ExpandProperty FullName
}
if (-not $python) { throw 'Python executable was not found after installation' }
New-Item -ItemType Directory -Path 'C:\DistrohopLab' -Force | Out-Null
Set-Content -LiteralPath 'C:\DistrohopLab\python-path.txt' -Encoding ascii -Value $python
& $python -c "import tkinter; assert tkinter.TkVersion >= 8.6"
if ($LASTEXITCODE -ne 0) { throw 'Python Tk runtime smoke failed' }
'ready' | Set-Content -LiteralPath 'C:\DistrohopLab\setup-ready.txt' -Encoding ascii
"""
    _ssh(root, script)
    print("Windows Python and Tk are ready")


def command_install_browsers(root: Path) -> None:
    script = r"""$ErrorActionPreference = 'Stop'
$ids = @('Brave.Brave', 'Mozilla.Firefox')
foreach ($id in $ids) {
  winget.exe install --id $id --exact --source winget --silent `
    --accept-package-agreements --accept-source-agreements
  if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne -1978335189) {
    throw "WinGet install failed for $id with $LASTEXITCODE"
  }
}
"""
    _ssh(root, script)
    print("Brave and Firefox are installed")


def command_sync(root: Path) -> None:
    revision = _run(
        ("git", "rev-parse", "--verify", "HEAD"),
        capture=True,
    ).stdout.strip()
    archive = _run(
        ("git", "archive", "--format=tar", "HEAD"),
        capture=True,
        text=False,
    ).stdout
    script = r"""$ErrorActionPreference = 'Stop'
$new = Join-Path $env:USERPROFILE 'distrohop.new'
$current = Join-Path $env:USERPROFILE 'distrohop'
$old = Join-Path $env:USERPROFILE 'distrohop.old'
Remove-Item -LiteralPath $new -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $new -Force | Out-Null
tar.exe -xf - -C $new
if ($LASTEXITCODE -ne 0) { throw 'tar extraction failed' }
Remove-Item -LiteralPath $old -Recurse -Force -ErrorAction SilentlyContinue
if (Test-Path $current) { Move-Item -LiteralPath $current -Destination $old }
Move-Item -LiteralPath $new -Destination $current
"""
    _ssh(root, script, input_value=archive, text=False)
    print("Synced committed revision {} to Windows".format(revision[:12]))


def command_test(root: Path) -> None:
    selected = paths(root)
    script = r"""$ErrorActionPreference = 'Stop'
$python = (Get-Content -LiteralPath 'C:\DistrohopLab\python-path.txt' -Raw).Trim()
Set-Location (Join-Path $env:USERPROFILE 'distrohop')
& $python -m tools.windows_smoke
if ($LASTEXITCODE -ne 0) { throw "Windows smoke failed: $LASTEXITCODE" }
"""
    result = _ssh(root, script, capture=True)
    start = result.stdout.find("{")
    if start < 0:
        raise LabError("Windows smoke did not return JSON")
    try:
        report = json.loads(result.stdout[start:])
    except ValueError as error:
        raise LabError("Windows smoke returned invalid JSON") from error
    if report.get("ok") is not True:
        raise LabError("Windows smoke did not confirm success")
    _private_directory(selected["reports"])
    target = selected["reports"] / "windows-11.json"
    target.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    target.chmod(0o600)
    print(json.dumps(report, indent=2, sort_keys=True))
    print("Windows report: {}".format(target))


def _wait_remote_json(
    root: Path,
    remote_path: str,
    *,
    statuses: Sequence[str],
    timeout: int,
) -> Dict[str, Any]:
    deadline = time.monotonic() + timeout
    wanted = set(statuses)
    while time.monotonic() < deadline:
        script = (
            "$p={}; if (Test-Path -LiteralPath $p) "
            "{{ Get-Content -LiteralPath $p -Raw }}".format(
                repr(remote_path)
            )
        )
        result = _ssh(root, script, check=False, capture=True)
        if result.returncode == 0 and result.stdout.strip():
            try:
                value = json.loads(result.stdout.strip())
            except ValueError:
                value = {}
            if value.get("status") in wanted:
                return value
        time.sleep(3)
    raise LabError(
        "Windows did not produce {} with status {}".format(
            remote_path,
            sorted(wanted),
        )
    )


def command_gui(root: Path) -> None:
    selected = paths(root)
    launch = r"""$ErrorActionPreference = 'Stop'
$python = (Get-Content -LiteralPath 'C:\DistrohopLab\python-path.txt' -Raw).Trim()
$repo = Join-Path $env:USERPROFILE 'distrohop'
$report = 'C:\DistrohopLab\gui-report.json'
Remove-Item -LiteralPath $report -Force -ErrorAction SilentlyContinue
$command = 'cmd.exe /c cd /d "' + $repo + '" && "' + $python +
  '" -m tools.windows_gui_smoke --report "' + $report +
  '" --hold 60 > C:\DistrohopLab\gui.log 2>&1'
$runOnce = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\RunOnce'
New-Item -Path $runOnce -Force | Out-Null
New-ItemProperty -Path $runOnce -Name DistrohopGuiSmoke -PropertyType String `
  -Value $command -Force | Out-Null
Restart-Computer -Force
"""
    _ssh(root, launch, check=False)
    report = _wait_remote_json(
        root,
        r"C:\DistrohopLab\gui-report.json",
        statuses=("ready", "failed"),
        timeout=300,
    )
    if report.get("status") == "failed" or report.get("ok") is not True:
        raise LabError("Windows GUI smoke failed: {}".format(report.get("error")))
    _private_directory(selected["reports"])
    screenshot = selected["reports"] / "windows-gui.png"
    command_screenshot(root, screenshot)
    complete = _wait_remote_json(
        root,
        r"C:\DistrohopLab\gui-report.json",
        statuses=("complete", "failed"),
        timeout=120,
    )
    target = selected["reports"] / "windows-gui.json"
    target.write_text(
        json.dumps(complete, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    target.chmod(0o600)
    if complete.get("status") != "complete" or complete.get("ok") is not True:
        raise LabError("Windows GUI did not complete successfully")
    print(json.dumps(complete, indent=2, sort_keys=True))
    print("Windows GUI report: {}".format(target))


def command_defender_dialog(root: Path) -> None:
    selected = paths(root)
    launch = r"""$ErrorActionPreference = 'Stop'
$python = (Get-Content -LiteralPath 'C:\DistrohopLab\python-path.txt' -Raw).Trim()
$repo = Join-Path $env:USERPROFILE 'distrohop'
Remove-Item -LiteralPath (Join-Path $repo '.distrohop-bootstrap.json') -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path $repo '.distrohop-bootstrap.log') -Force -ErrorAction SilentlyContinue
$command = 'cmd.exe /c cd /d "' + $repo + '" && set DISTROHOP_LANGUAGE=pt ' +
  '&& "' + $python + '" -m distrohop.bootstrap --cli list --json ' +
  '> C:\DistrohopLab\defender-cli.log 2>&1'
$runOnce = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\RunOnce'
New-Item -Path $runOnce -Force | Out-Null
New-ItemProperty -Path $runOnce -Name DistrohopDefenderSmoke -PropertyType String `
  -Value $command -Force | Out-Null
Restart-Computer -Force
"""
    _ssh(root, launch, check=False)
    deadline = time.monotonic() + 300
    dialog_seen = False
    while time.monotonic() < deadline:
        result = _ssh(
            root,
            "$p=Get-Process python* -ErrorAction SilentlyContinue; "
            "if ($p) { 'running' }",
            check=False,
            capture=True,
        )
        if result.returncode == 0 and result.stdout.strip() == "running":
            dialog_seen = True
            break
        time.sleep(3)
    if not dialog_seen:
        raise LabError("Defender consent dialog process did not appear")
    time.sleep(5)
    _private_directory(selected["reports"])
    screenshot = selected["reports"] / "windows-defender-dialog.png"
    command_screenshot(root, screenshot)
    _monitor(root, "sendkey tab")
    time.sleep(1)
    _monitor(root, "sendkey ret")
    deadline = time.monotonic() + 120
    state = {}
    while time.monotonic() < deadline:
        result = _ssh(
            root,
            "$p=Join-Path $env:USERPROFILE 'distrohop\\.distrohop-bootstrap.json'; "
            "if (Test-Path -LiteralPath $p) { Get-Content -LiteralPath $p -Raw }",
            check=False,
            capture=True,
        )
        try:
            state = json.loads(result.stdout.strip()) if result.stdout.strip() else {}
        except ValueError:
            state = {}
        if state.get("continued_without_exclusion") is True:
            break
        time.sleep(3)
    if state.get("continued_without_exclusion") is not True:
        raise LabError("Defender dialog did not record safe continue choice")
    report = {
        "ok": True,
        "dialog_seen": True,
        "continued_without_exclusion": True,
        "exclusion_requested": False,
    }
    target = selected["reports"] / "windows-defender-dialog.json"
    target.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    target.chmod(0o600)
    print(json.dumps(report, indent=2, sort_keys=True))
    print("Windows Defender dialog report: {}".format(target))


def _monitor(root: Path, command: str) -> str:
    selected = paths(root)
    if not selected["monitor"].exists():
        raise LabError("QEMU monitor socket is unavailable")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(10)
        client.connect(str(selected["monitor"]))
        client.recv(4096)
        client.sendall(command.encode("ascii") + b"\n")
        time.sleep(0.25)
        return client.recv(65536).decode(errors="replace")


def command_screenshot(root: Path, destination: Path) -> None:
    target = destination.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    ppm = target.with_suffix(".ppm")
    _monitor(root, "screendump {}".format(ppm))
    _run(("convert", str(ppm), str(target)))
    ppm.unlink()
    print("Windows screenshot: {}".format(target))


def command_stop(root: Path) -> None:
    selected = paths(root)
    pid = _pid(selected)
    if pid is None:
        print("Windows VM is already stopped")
        return
    _ssh(root, "Stop-Computer -Force", check=False)
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if _pid(selected) is None:
            print("Stopped Windows VM")
            return
        time.sleep(1)
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if _pid(selected) is None:
            print("Stopped Windows VM after exact QEMU SIGTERM")
            return
        time.sleep(1)
    raise LabError("Windows QEMU PID {} did not stop".format(pid))


def command_destroy(root: Path, confirmed: bool) -> None:
    selected = paths(root)
    if not confirmed:
        raise LabError("destroy requires --yes")
    if _pid(selected) is not None:
        raise LabError("Windows VM is running; stop it first")
    expected = (root / "instance").resolve()
    actual = selected["instance"].resolve()
    if actual != expected:
        raise LabError("refusing unsafe Windows instance path")
    if actual.exists():
        shutil.rmtree(actual)
    print("Destroyed disposable Windows instance; the verified ISO was preserved")


def command_doctor(root: Path) -> None:
    tools = (
        "curl",
        "qemu-system-x86_64",
        "qemu-img",
        "ssh",
        "ssh-keygen",
        "mkfs.vfat",
        "mcopy",
        "convert",
    )
    missing = [tool for tool in tools if shutil.which(tool) is None]
    result = {
        "ok": not missing,
        "missing": missing,
        "memory_mb": MEMORY_MB,
        "cpus": CPUS,
        "disk_gib": DISK_GIB,
        "state": str(root),
        "iso": paths(root)["iso"].is_file(),
        "running": _pid(paths(root)) is not None,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    if missing:
        raise LabError("missing host tools: {}".format(", ".join(missing)))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--state-dir")
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor")
    commands.add_parser("fetch")
    commands.add_parser("create")
    start = commands.add_parser("start")
    start.add_argument("--installer", action="store_true")
    wait = commands.add_parser("wait")
    wait.add_argument("--timeout", type=int, default=3600)
    commands.add_parser("setup")
    commands.add_parser("install-browsers")
    commands.add_parser("sync")
    commands.add_parser("test")
    commands.add_parser("gui")
    commands.add_parser("defender-dialog")
    screenshot = commands.add_parser("screenshot")
    screenshot.add_argument("destination")
    commands.add_parser("stop")
    destroy = commands.add_parser("destroy")
    destroy.add_argument("--yes", action="store_true")
    return result


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = parser().parse_args(argv)
    root = (
        Path(arguments.state_dir).expanduser().resolve()
        if arguments.state_dir
        else state_root()
    )
    _private_directory(root)
    try:
        if arguments.command == "doctor":
            command_doctor(root)
        elif arguments.command == "fetch":
            command_fetch(root)
        elif arguments.command == "create":
            command_create(root)
        elif arguments.command == "start":
            command_start(root, installer=arguments.installer)
        elif arguments.command == "wait":
            command_wait(root, max(1, arguments.timeout))
        elif arguments.command == "setup":
            command_setup(root)
        elif arguments.command == "install-browsers":
            command_install_browsers(root)
        elif arguments.command == "sync":
            command_sync(root)
        elif arguments.command == "test":
            command_test(root)
        elif arguments.command == "gui":
            command_gui(root)
        elif arguments.command == "defender-dialog":
            command_defender_dialog(root)
        elif arguments.command == "screenshot":
            command_screenshot(root, Path(arguments.destination))
        elif arguments.command == "stop":
            command_stop(root)
        elif arguments.command == "destroy":
            command_destroy(root, arguments.yes)
        return 0
    except LabError as error:
        print("windows-vm-lab: {}".format(error), file=os.sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
