"""Paranoid Linux-only planning and execution for a Distrohop vault partition."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Sequence, Tuple

from distrohop.vault.bundle import verify_bundle
from distrohop.vault.targets import publish_to_targets
from distrohop.platform_ import current_platform


VAULT_LABEL = "DISTROHOP-DO-NOT-FORMAT"
FILESYSTEM_LABEL = "DISTROHOP-DO-NOT"
CONFIRMATION_PHRASE = "EU ENTENDO E VOU USAR PARTICIONAMENTO MANUAL"
LINUX_FILESYSTEM_GUID = "0FC63DAF-8483-4772-8E79-3D69D8477DE4"
MINIMUM_SIZE = 1024**3
MARGIN_NUMERATOR = 6
MARGIN_DENOMINATOR = 5

WARNING = (
    "Uma partição-cofre só sobrevive se, ao instalar a nova distro, você "
    "escolher particionamento manual e NÃO marcar esta partição para formatar. "
    "Se escolher apagar o disco inteiro, o instalador destrói o cofre junto. "
    "Se não pretende usar particionamento manual, use um destino externo."
)

VAULT_README = """NÃO FORMATAR — COFRE DISTROHOP

Esta partição só sobrevive à instalação de outra distribuição se você escolher
PARTICIONAMENTO MANUAL e NÃO marcar esta partição para formatação.

O modo "apagar o disco inteiro" também apaga este cofre. Não existe atributo
GPT capaz de impedir isso. Mantenha sempre outra cópia verificada do bundle.
"""


class VaultError(RuntimeError):
    pass


@dataclass(frozen=True)
class Partition:
    node: str
    number: int
    start: int
    size: int
    name: str = ""
    filesystem: str = ""
    mountpoint: str = ""

    @property
    def end(self) -> int:
        return self.start + self.size


@dataclass(frozen=True)
class DiskLayout:
    device: Path
    label: str
    sector_size: int
    first_lba: int
    last_lba: int
    partitions: Tuple[Partition, ...]

    @property
    def end(self) -> int:
        return self.last_lba + 1

    def fingerprint(self) -> str:
        payload = {
            "device": str(self.device),
            "label": self.label,
            "sector_size": self.sector_size,
            "first_lba": self.first_lba,
            "last_lba": self.last_lba,
            "partitions": [
                {
                    "node": item.node,
                    "number": item.number,
                    "start": item.start,
                    "size": item.size,
                    "name": item.name,
                }
                for item in self.partitions
            ],
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True)
class BtrfsState:
    partition_node: str
    mountpoint: str
    filesystem: str
    free_bytes: int
    device_count: int = 1
    writable: bool = True
    balance_running: bool = False
    scrub_running: bool = False
    snapshot_running: bool = False


@dataclass(frozen=True)
class PlannedCommand:
    arguments: Tuple[str, ...]
    stdin: str = ""
    description: str = ""


@dataclass(frozen=True)
class VaultPlan:
    disk: Path
    size_bytes: int
    backup_bundle: Path
    strategy: str
    start_sector: int
    size_sectors: int
    partition_number: int
    partition_node: Path
    layout_fingerprint: str
    original_layout: DiskLayout
    commands: Tuple[PlannedCommand, ...]
    source_partition: Optional[Partition] = None
    source_mountpoint: str = ""
    shrink_bytes: int = 0


Runner = Callable[..., subprocess.CompletedProcess]
_geteuid = getattr(os, "geteuid", lambda: -1)


def _partition_number(disk: Path, node: str) -> int:
    prefix = str(disk)
    if not node.startswith(prefix):
        raise VaultError("partição {} não pertence a {}".format(node, disk))
    suffix = node[len(prefix):]
    if suffix.startswith("p"):
        suffix = suffix[1:]
    if not suffix.isdigit() or int(suffix) <= 0:
        raise VaultError("número de partição não reconhecido: {}".format(node))
    return int(suffix)


def _partition_node(disk: Path, number: int) -> Path:
    separator = "p" if disk.name[-1:].isdigit() else ""
    return Path("{}{}{}".format(disk, separator, number))


def parse_layout(
    text: str,
    disk: Path,
    *,
    filesystem_info: Optional[Mapping[str, Mapping[str, str]]] = None,
) -> DiskLayout:
    try:
        table = json.loads(text)["partitiontable"]
        label = str(table["label"]).casefold()
        sector_size = int(table.get("sectorsize") or 512)
        first_lba = int(table["firstlba"])
        last_lba = int(table["lastlba"])
        raw_partitions = table.get("partitions") or []
    except (ValueError, TypeError, KeyError) as error:
        raise VaultError("tabela de partições inválida: {}".format(error)) from error
    if sector_size not in (512, 1024, 2048, 4096):
        raise VaultError("tamanho de setor não suportado: {}".format(sector_size))
    details = filesystem_info or {}
    partitions = []
    for raw in raw_partitions:
        try:
            node = str(raw["node"])
            start = int(raw["start"])
            size = int(raw["size"])
        except (TypeError, ValueError, KeyError) as error:
            raise VaultError("entrada de partição inválida") from error
        info = details.get(node, {})
        partitions.append(
            Partition(
                node=node,
                number=_partition_number(disk, node),
                start=start,
                size=size,
                name=str(raw.get("name") or ""),
                filesystem=str(info.get("filesystem") or ""),
                mountpoint=str(info.get("mountpoint") or ""),
            )
        )
    partitions.sort(key=lambda item: item.start)
    previous_end = first_lba
    for item in partitions:
        if item.start < previous_end or item.end > last_lba + 1:
            raise VaultError("partições sobrepostas ou fora dos limites GPT")
        previous_end = item.end
    return DiskLayout(
        device=disk,
        label=label,
        sector_size=sector_size,
        first_lba=first_lba,
        last_lba=last_lba,
        partitions=tuple(partitions),
    )


def _align_up(value: int, alignment: int) -> int:
    return ((value + alignment - 1) // alignment) * alignment


def _align_down(value: int, alignment: int) -> int:
    return (value // alignment) * alignment


def _next_partition_number(partitions: Iterable[Partition]) -> int:
    used = {item.number for item in partitions}
    for number in range(1, 129):
        if number not in used:
            return number
    raise VaultError("a tabela GPT não tem entrada de partição livre")


def _commands(
    layout: DiskLayout,
    *,
    start_sector: int,
    size_sectors: int,
    partition_number: int,
    source: Optional[Partition],
    source_mountpoint: str,
    shrink_bytes: int,
) -> Tuple[PlannedCommand, ...]:
    disk = str(layout.device)
    commands = []
    if source is not None:
        commands.extend(
            (
                PlannedCommand(
                    (
                        "btrfs",
                        "filesystem",
                        "resize",
                        "-{}".format(shrink_bytes),
                        source_mountpoint,
                    ),
                    description="reduzir o Btrfs antes da partição",
                ),
                PlannedCommand(
                    (
                        "sfdisk",
                        "--no-reread",
                        "--no-tell-kernel",
                        "--lock",
                        "-N",
                        str(source.number),
                        disk,
                    ),
                    stdin="size={}\n".format(source.size - shrink_bytes // layout.sector_size),
                    description="reduzir somente o final da partição Btrfs",
                ),
                PlannedCommand(
                    (
                        "partx",
                        "--update",
                        "--nr",
                        str(source.number),
                        disk,
                    ),
                    description="atualizar o tamanho no kernel",
                ),
            )
        )
    commands.extend(
        (
            PlannedCommand(
                (
                    "sfdisk",
                    "--append",
                    "--no-reread",
                    "--no-tell-kernel",
                    "--lock",
                    "-N",
                    str(partition_number),
                    disk,
                ),
                stdin=(
                    'start={}, size={}, type={}, name="{}"\n'.format(
                        start_sector,
                        size_sectors,
                        LINUX_FILESYSTEM_GUID,
                        VAULT_LABEL,
                    )
                ),
                description="adicionar uma única entrada GPT no espaço livre",
            ),
            PlannedCommand(
                (
                    "partx",
                    "--add",
                    "--nr",
                    str(partition_number),
                    disk,
                ),
                description="informar a nova partição ao kernel",
            ),
            PlannedCommand(
                ("udevadm", "settle"),
                description="aguardar a criação do dispositivo",
            ),
            PlannedCommand(
                (
                    "mkfs.ext4",
                    "-F",
                    "-L",
                    FILESYSTEM_LABEL,
                    str(_partition_node(layout.device, partition_number)),
                ),
                description="formatar somente a nova partição como ext4",
            ),
        )
    )
    return tuple(commands)


def build_plan(
    layout: DiskLayout,
    *,
    size_bytes: int,
    backup_bundle: Path,
    confirmation: str,
    backup_valid: bool,
    backup_independent: bool,
    btrfs: Optional[BtrfsState] = None,
    minimum_size: int = MINIMUM_SIZE,
) -> VaultPlan:
    """Pure safety policy. The confirmation is deliberately checked first."""
    if confirmation != CONFIRMATION_PHRASE:
        raise VaultError(
            "confirmação incorreta; digite exatamente: {}".format(
                CONFIRMATION_PHRASE
            )
        )
    if not backup_valid:
        raise VaultError("a segunda cópia não é um bundle íntegro")
    if not backup_independent:
        raise VaultError("a segunda cópia está no mesmo disco escolhido")
    if layout.label != "gpt":
        raise VaultError("a partição-cofre exige tabela GPT existente")
    if size_bytes < minimum_size:
        raise VaultError(
            "o cofre precisa ter pelo menos {} bytes".format(minimum_size)
        )
    if any(item.size <= 0 for item in layout.partitions):
        raise VaultError("a tabela contém partição com tamanho inválido")
    if any(item.name == VAULT_LABEL for item in layout.partitions):
        raise VaultError("já existe uma entrada GPT com o nome da partição-cofre")

    alignment = max(1, 1024 * 1024 // layout.sector_size)
    requested = math.ceil(size_bytes / layout.sector_size)
    requested = _align_up(requested, alignment)
    required_gap = math.ceil(
        requested * MARGIN_NUMERATOR / MARGIN_DENOMINATOR
    )
    previous_end = (
        layout.partitions[-1].end
        if layout.partitions
        else layout.first_lba
    )
    free_start = _align_up(previous_end, alignment)
    tail_free = layout.end - free_start
    source: Optional[Partition] = None
    source_mountpoint = ""
    shrink_bytes = 0
    strategy = "free-space"

    if tail_free < required_gap:
        strategy = "shrink-btrfs"
        if not layout.partitions:
            raise VaultError("espaço livre GPT insuficiente para o cofre e a margem")
        source = layout.partitions[-1]
        if btrfs is None or btrfs.partition_node != source.node:
            raise VaultError(
                "o espaço livre é insuficiente e a última partição não pôde ser inspecionada"
            )
        if btrfs.filesystem.casefold() != "btrfs":
            raise VaultError(
                "encolhimento automático recusado: o filesystem não é btrfs; "
                "use live USB/GParted"
            )
        if not btrfs.mountpoint or not btrfs.writable:
            raise VaultError("o Btrfs precisa estar montado em modo leitura/escrita")
        if btrfs.device_count != 1:
            raise VaultError("Btrfs com múltiplos dispositivos não é encolhido automaticamente")
        if btrfs.balance_running:
            raise VaultError("há balance Btrfs em andamento")
        if btrfs.scrub_running:
            raise VaultError("há scrub Btrfs em andamento")
        if btrfs.snapshot_running:
            raise VaultError("há snapshot/send Btrfs em andamento")
        required_free_bytes = (
            requested
            * layout.sector_size
            * MARGIN_NUMERATOR
            // MARGIN_DENOMINATOR
        )
        if btrfs.free_bytes < required_free_bytes:
            raise VaultError(
                "espaço livre real do Btrfs é menor que o tamanho pedido + 20%"
            )
        new_end = _align_down(layout.end - required_gap, alignment)
        if new_end <= source.start:
            raise VaultError("a redução deixaria a partição Btrfs inválida")
        shrink_sectors = source.end - new_end
        if shrink_sectors <= 0:
            raise VaultError("não foi possível calcular uma redução segura")
        shrink_bytes = shrink_sectors * layout.sector_size
        free_start = new_end
        source_mountpoint = btrfs.mountpoint

    if free_start + requested > layout.end:
        raise VaultError("não há espaço contíguo suficiente no final do disco")
    number = _next_partition_number(layout.partitions)
    commands = _commands(
        layout,
        start_sector=free_start,
        size_sectors=requested,
        partition_number=number,
        source=source,
        source_mountpoint=source_mountpoint,
        shrink_bytes=shrink_bytes,
    )
    forbidden = ("grub", "bootctl", "efibootmgr", "fstab", "--reorder")
    command_text = "\n".join(" ".join(item.arguments) for item in commands)
    if any(item in command_text for item in forbidden):
        raise AssertionError("o plano tentou alterar boot/fstab/ordem")
    return VaultPlan(
        disk=layout.device,
        size_bytes=requested * layout.sector_size,
        backup_bundle=Path(backup_bundle),
        strategy=strategy,
        start_sector=free_start,
        size_sectors=requested,
        partition_number=number,
        partition_node=_partition_node(layout.device, number),
        layout_fingerprint=layout.fingerprint(),
        original_layout=layout,
        commands=commands,
        source_partition=source,
        source_mountpoint=source_mountpoint,
        shrink_bytes=shrink_bytes,
    )


def _run(
    runner: Runner,
    arguments: Sequence[str],
    *,
    stdin: str = "",
    check: bool = True,
    timeout: int = 120,
) -> subprocess.CompletedProcess:
    try:
        result = runner(
            list(arguments),
            input=stdin or None,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise VaultError(
            "{} não pôde ser executado: {}".format(arguments[0], error)
        ) from error
    if check and result.returncode:
        detail = (result.stderr or result.stdout or "").strip()
        raise VaultError(
            "{} falhou: {}".format(" ".join(arguments), detail or result.returncode)
        )
    return result


def _filesystem_details(disk: Path, runner: Runner) -> Dict[str, Dict[str, str]]:
    result = _run(
        runner,
        (
            "lsblk",
            "-J",
            "-b",
            "-o",
            "PATH,FSTYPE,MOUNTPOINTS",
            str(disk),
        ),
    )
    try:
        roots = json.loads(result.stdout).get("blockdevices") or []
    except (ValueError, TypeError) as error:
        raise VaultError("JSON do lsblk inválido") from error
    details: Dict[str, Dict[str, str]] = {}

    def walk(items: Iterable[Mapping[str, Any]]) -> None:
        for item in items:
            path = str(item.get("path") or "")
            mounts = item.get("mountpoints") or []
            if isinstance(mounts, str):
                mounts = [mounts]
            if path:
                details[path] = {
                    "filesystem": str(item.get("fstype") or ""),
                    "mountpoint": next(
                        (str(value) for value in mounts if value),
                        "",
                    ),
                }
            walk(item.get("children") or [])

    walk(roots)
    return details


def inspect_layout(disk: Path, runner: Runner = subprocess.run) -> DiskLayout:
    details = {} if disk.is_file() else _filesystem_details(disk, runner)
    result = _run(runner, ("sfdisk", "--json", str(disk)))
    return parse_layout(result.stdout, disk, filesystem_info=details)


def _btrfs_state(partition: Partition, runner: Runner) -> BtrfsState:
    if partition.filesystem.casefold() != "btrfs" or not partition.mountpoint:
        return BtrfsState(
            partition_node=partition.node,
            mountpoint="",
            filesystem=partition.filesystem,
            free_bytes=0,
            writable=False,
        )
    mount = partition.mountpoint
    findmnt = _run(
        runner,
        ("findmnt", "-J", "-o", "TARGET,SOURCE,FSTYPE,OPTIONS", "--target", mount),
    )
    try:
        filesystems = json.loads(findmnt.stdout).get("filesystems") or []
        options = str(filesystems[0].get("options") or "").split(",")
    except (ValueError, TypeError, IndexError, AttributeError) as error:
        raise VaultError("findmnt não confirmou o Btrfs montado") from error
    usage = _run(
        runner,
        ("btrfs", "filesystem", "usage", "-b", mount),
    )
    match = re.search(r"Free \\(estimated\\):\\s*([0-9]+)", usage.stdout)
    if not match:
        raise VaultError("btrfs não informou espaço livre estimado em bytes")
    show = _run(runner, ("btrfs", "filesystem", "show", "--raw", mount))
    device_count = len(re.findall(r"^\\s*devid\\s+", show.stdout, re.MULTILINE))
    balance = _run(runner, ("btrfs", "balance", "status", mount), check=False)
    scrub = _run(runner, ("btrfs", "scrub", "status", mount), check=False)
    processes = _run(runner, ("ps", "-eo", "comm=,args="))
    return BtrfsState(
        partition_node=partition.node,
        mountpoint=mount,
        filesystem=partition.filesystem,
        free_bytes=int(match.group(1)),
        device_count=device_count,
        writable="rw" in options,
        balance_running=(
            balance.returncode == 0
            and "no balance found" not in (balance.stdout or "").casefold()
        ),
        scrub_running=bool(
            re.search(r"status:\\s*running", scrub.stdout or "", re.IGNORECASE)
        ),
        snapshot_running=bool(
            re.search(
                r"\\bbtrfs\\s+(?:subvolume\\s+snapshot|send)\\b",
                processes.stdout or "",
                re.IGNORECASE,
            )
        ),
    )


def _backup_is_independent(
    bundle: Path,
    disk: Path,
    runner: Runner,
) -> bool:
    source = _run(
        runner,
        ("findmnt", "-n", "-o", "SOURCE", "--target", str(bundle)),
        check=False,
    )
    value = (source.stdout or "").strip()
    value = value.split("[", 1)[0]
    if not value or not value.startswith("/dev/"):
        return True
    if value == str(disk):
        return False
    ancestry = _run(
        runner,
        ("lsblk", "-s", "-n", "-o", "PATH", value),
        check=False,
    )
    if ancestry.returncode:
        return False
    devices = {
        str(Path(line.strip()).resolve())
        for line in (ancestry.stdout or "").splitlines()
        if line.strip().startswith("/dev/")
    }
    return bool(devices) and str(disk.resolve()) not in devices


def plan_vault(
    disk: Path,
    *,
    size_bytes: int,
    backup_bundle: Path,
    confirmation: str,
    runner: Runner = subprocess.run,
    platform_name: Optional[str] = None,
    allow_regular_file: bool = False,
    minimum_size: int = MINIMUM_SIZE,
) -> VaultPlan:
    # The warning/confirmation gate precedes every disk and bundle probe.
    if confirmation != CONFIRMATION_PHRASE:
        raise VaultError(
            "{} Digite exatamente: {}".format(WARNING, CONFIRMATION_PHRASE)
        )
    selected_platform = platform_name or current_platform()
    if selected_platform != "linux":
        raise VaultError("partição-cofre existe somente no Linux")
    disk = Path(disk).resolve()
    try:
        mode = disk.stat().st_mode
    except OSError as error:
        raise VaultError("disco não encontrado: {}".format(disk)) from error
    if not stat.S_ISBLK(mode) and not (
        allow_regular_file and stat.S_ISREG(mode)
    ):
        raise VaultError("o alvo não é um dispositivo de bloco")
    backup = Path(backup_bundle).resolve()
    backup_valid = verify_bundle(backup)
    independent = (
        _backup_is_independent(backup, disk, runner)
        if backup_valid
        else False
    )
    layout = inspect_layout(disk, runner)
    btrfs = None
    alignment = max(1, 1024 * 1024 // layout.sector_size)
    requested = _align_up(
        math.ceil(size_bytes / layout.sector_size),
        alignment,
    )
    previous_end = (
        layout.partitions[-1].end
        if layout.partitions
        else layout.first_lba
    )
    tail = layout.end - _align_up(previous_end, alignment)
    required = math.ceil(
        requested * MARGIN_NUMERATOR / MARGIN_DENOMINATOR
    )
    if tail < required and layout.partitions:
        btrfs = _btrfs_state(layout.partitions[-1], runner)
    return build_plan(
        layout,
        size_bytes=size_bytes,
        backup_bundle=backup,
        confirmation=confirmation,
        backup_valid=backup_valid,
        backup_independent=independent,
        btrfs=btrfs,
        minimum_size=minimum_size,
    )


def render_plan(plan: VaultPlan) -> str:
    lines = [
        "DRY-RUN — nenhum disco será alterado.",
        WARNING,
        "Estratégia: {}".format(plan.strategy),
        "Disco: {}".format(plan.disk),
        "Nova partição: {} ({} bytes)".format(
            plan.partition_node,
            plan.size_bytes,
        ),
        "Segunda cópia íntegra: {}".format(plan.backup_bundle),
    ]
    if plan.shrink_bytes:
        lines.append(
            "Redução Btrfs planejada: {} bytes em {}".format(
                plan.shrink_bytes,
                plan.source_mountpoint,
            )
        )
    lines.append("COMANDOS PLANEJADOS:")
    for command in plan.commands:
        lines.append("  {}".format(" ".join(command.arguments)))
        if command.stdin:
            lines.append("    stdin: {}".format(command.stdin.strip()))
    lines.extend(
        (
            "  mount {} <temporário>".format(plan.partition_node),
            "  copiar e verificar {} no cofre".format(plan.backup_bundle.name),
            "  umount <temporário>",
        )
    )
    return "\n".join(lines)


def _run_planned(command: PlannedCommand, runner: Runner) -> None:
    _run(
        runner,
        command.arguments,
        stdin=command.stdin,
        timeout=3600,
    )


def _validate_written_layout(plan: VaultPlan, runner: Runner) -> None:
    result = _run(runner, ("sfdisk", "--json", str(plan.disk)))
    written = parse_layout(result.stdout, plan.disk)
    if len(written.partitions) != len(plan.original_layout.partitions) + 1:
        raise VaultError(
            "a quantidade de partições mudou de forma inesperada; mkfs foi bloqueado"
        )
    by_number = {item.number: item for item in written.partitions}
    for original in plan.original_layout.partitions:
        current = by_number.get(original.number)
        expected_size = (
            original.size
            - plan.shrink_bytes // plan.original_layout.sector_size
            if plan.source_partition is not None
            and original.number == plan.source_partition.number
            else original.size
        )
        if (
            current is None
            or current.start != original.start
            or current.size != expected_size
            or current.name != original.name
        ):
            raise VaultError(
                "uma partição existente divergiu do plano; mkfs foi bloqueado"
            )
    created = next(
        (
            item
            for item in written.partitions
            if item.number == plan.partition_number
        ),
        None,
    )
    if (
        created is None
            or created.start != plan.start_sector
            or created.size != plan.size_sectors
            or Path(created.node) != plan.partition_node
            or created.name != VAULT_LABEL
    ):
        raise VaultError(
            "a nova entrada GPT não corresponde ao plano; mkfs foi bloqueado"
        )


def create_vault(
    plan: VaultPlan,
    *,
    confirmation: str,
    runner: Runner = subprocess.run,
    geteuid: Callable[[], int] = _geteuid,
) -> Dict[str, Any]:
    if confirmation != CONFIRMATION_PHRASE:
        raise VaultError("confirmação por extenso não confere")
    if geteuid() != 0:
        raise VaultError(
            "criação do cofre exige root; revise o dry-run e execute a CLI com sudo"
        )
    if not verify_bundle(plan.backup_bundle):
        raise VaultError("a segunda cópia deixou de ser íntegra")
    if not _backup_is_independent(plan.backup_bundle, plan.disk, runner):
        raise VaultError("a segunda cópia deixou de ser independente")
    current = inspect_layout(plan.disk, runner)
    if current.fingerprint() != plan.layout_fingerprint:
        raise VaultError("a tabela de partições mudou desde o planejamento")
    if plan.source_partition is not None:
        source = next(
            (
                item
                for item in current.partitions
                if item.number == plan.source_partition.number
            ),
            None,
        )
        if source is None:
            raise VaultError("a partição Btrfs de origem desapareceu")
        state = _btrfs_state(source, runner)
        if state.filesystem.casefold() != "btrfs" or not state.writable:
            raise VaultError("o Btrfs deixou de estar montado em leitura/escrita")
        if state.device_count != 1:
            raise VaultError("o Btrfs passou a usar múltiplos dispositivos")
        if state.balance_running or state.scrub_running or state.snapshot_running:
            raise VaultError("uma operação Btrfs começou depois do planejamento")
        required_free = (
            plan.size_bytes * MARGIN_NUMERATOR // MARGIN_DENOMINATOR
        )
        if state.free_bytes < required_free:
            raise VaultError("o espaço livre Btrfs caiu depois do planejamento")
    if plan.partition_node.exists():
        raise VaultError(
            "o dispositivo planejado já existe; a tabela mudou"
        )

    dump = _run(runner, ("sfdisk", "--dump", str(plan.disk)))
    dump_path = plan.backup_bundle.parent / (
        "{}.before-distrohop.sfdisk".format(plan.disk.name)
    )
    dump_path.write_text(dump.stdout, encoding="utf-8")
    os.chmod(dump_path, 0o600)

    mounted = False
    mountpoint = Path(tempfile.mkdtemp(prefix="distrohop-vault-"))
    completed = []
    try:
        for command in plan.commands:
            if command.arguments[0] == "mkfs.ext4":
                _validate_written_layout(plan, runner)
            _run_planned(command, runner)
            completed.append(command.description)
        if not plan.partition_node.exists():
            raise VaultError(
                "o kernel não criou {}; reinicie e não formate nada".format(
                    plan.partition_node
                )
            )
        label = _run(
            runner,
            (
                "blkid",
                "-s",
                "LABEL",
                "-o",
                "value",
                str(plan.partition_node),
            ),
        )
        if (label.stdout or "").strip() != FILESYSTEM_LABEL:
            raise VaultError("o label ext4 não corresponde ao cofre planejado")
        _run(
            runner,
            ("mount", str(plan.partition_node), str(mountpoint)),
        )
        mounted = True
        (mountpoint / "README.txt").write_text(VAULT_README, encoding="utf-8")
        destinations = publish_to_targets(
            plan.backup_bundle,
            (mountpoint,),
            plan.backup_bundle.name,
            require_private_permissions=True,
        )
        if not destinations or not verify_bundle(destinations[0]):
            raise VaultError("a cópia escrita no cofre falhou na verificação")
        _run(runner, ("sync",))
        return {
            "partition": str(plan.partition_node),
            "gpt_name": VAULT_LABEL,
            "filesystem_label": FILESYSTEM_LABEL,
            "bundle": str(destinations[0]),
            "partition_table_backup": str(dump_path),
            "strategy": plan.strategy,
            "completed": completed,
        }
    except Exception as error:
        if isinstance(error, VaultError):
            raise
        raise VaultError("criação do cofre falhou: {}".format(error)) from error
    finally:
        if mounted:
            _run(
                runner,
                ("umount", str(mountpoint)),
                check=False,
            )
        shutil.rmtree(mountpoint, ignore_errors=True)
