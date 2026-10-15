#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

STATE = ".private/transfer/state.json"
MANIFEST = "__riftlab_transfer__/manifest.json"
TOOL = "__riftlab_transfer__/transfer_private.py"
CACHES = {
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "dist",
    "playwright-report",
    "test-results",
    "htmlcov",
    ".idea",
    ".vscode",
}


def fail(message: str) -> None:
    raise ValueError(message)


def digest_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(document, stream, ensure_ascii=True, sort_keys=True, indent=2)
        stream.write("\n")
    os.replace(temporary, path)


def safe_path(root: Path, name: str) -> Path:
    relative = PurePosixPath(name)
    if (
        not relative.parts
        or relative.is_absolute()
        or ".." in relative.parts
        or "\\" in name
    ):
        fail(f"Caminho inseguro: {name!r}")
    if (
        relative.parts[0] in {".git", "__riftlab_transfer__"}
        or name == ".private/transfer"
        or name.startswith(".private/transfer/")
    ):
        fail(f"Caminho reservado: {name!r}")
    destination = root.joinpath(*relative.parts)
    for parent in destination.parents:
        if parent == root:
            break
        if parent.is_symlink():
            fail(f"Diretório é um link simbólico: {name}")
    if not destination.parent.resolve().is_relative_to(root):
        fail(f"Diretório aponta para fora do projeto: {name}")
    return destination


def state_path(root: Path) -> Path:
    for parent in (root / ".private", root / ".private/transfer"):
        if parent.is_symlink():
            fail("A pasta de controle da transferência não pode ser um link")
    control = root / STATE
    if control.is_symlink():
        fail("O manifesto local não pode ser um link")
    return control


def excluded(name: str, include_sensitive: bool) -> bool:
    path = PurePosixPath(name)
    if any(part in CACHES for part in path.parts):
        return True
    if name.startswith(".private/transfer/") or path.name.endswith(
        (".pyc", ".pyo", ".tsbuildinfo", ".gpg")
    ):
        return True
    if path.name in {".DS_Store", ".coverage"} or path.name.startswith(
        "riftlab-ignored-"
    ):
        return True
    if include_sensitive:
        return False
    return (
        path.name == ".aws"
        or ".aws" in path.parts
        or path.name.startswith(".env")
        and path.name != ".env.example"
        or path.suffix.lower() in {".pem", ".key", ".p12", ".pfx"}
        or path.name.startswith("riot-account")
        or "secret" in path.name.lower()
        or path.name in {"credentials", "id_rsa", "id_ed25519", "id_ecdsa"}
    )


def describe(path: Path, root: Path) -> dict | None:
    if path.is_symlink():
        target = os.readlink(path)
        resolved = path.resolve()
        if (
            Path(target).is_absolute()
            or not resolved.is_relative_to(root)
            or path.is_dir()
        ):
            return None
        if resolved.is_relative_to(root / ".git") or resolved.is_relative_to(
            root / ".private/transfer"
        ):
            return None
        return {
            "kind": "symlink",
            "target": target,
            "sha256": hashlib.sha256(os.fsencode(target)).hexdigest(),
            "size": 0,
        }
    if not path.is_file():
        return None
    metadata = path.stat()
    return {
        "kind": "file",
        "sha256": digest_file(path),
        "size": metadata.st_size,
        "mode": stat.S_IMODE(metadata.st_mode) & 0o777,
    }


def snapshot(root: Path, include_sensitive: bool) -> dict:
    result = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "ls-files",
            "--others",
            "--ignored",
            "--exclude-standard",
            "-z",
        ],
        check=True,
        capture_output=True,
    )
    entries = {}
    for raw in sorted(result.stdout.split(b"\0")):
        if not raw:
            continue
        name = os.fsdecode(raw)
        if excluded(name, include_sensitive):
            continue
        entry = describe(safe_path(root, name), root)
        if entry is not None:
            entries[name] = entry
    return {"version": 1, "includeSensitive": include_sensitive, "entries": entries}


def load_state(path: Path) -> dict:
    document = json.loads(path.read_text())
    if document.get("version") != 1 or not isinstance(document.get("entries"), dict):
        fail("Manifesto de transferência incompatível")
    return document


def export(
    root: Path, output: Path, full: bool, include_sensitive: bool, base: Path | None
) -> None:
    output = output.expanduser().resolve()
    if output.is_relative_to(root):
        fail(
            "Escolha um ZIP fora do projeto, para não incluir o pacote no próprio backup"
        )
    if output.exists():
        fail("O arquivo de saída já existe; escolha outro nome")
    control = state_path(root)
    if base is not None and not base.exists():
        fail("O manifesto informado em --base não existe")
    previous_path = base or control
    previous = (
        load_state(previous_path)
        if not full and previous_path.exists()
        else {"entries": {}}
    )
    current = snapshot(root, include_sensitive)
    changed = {
        name: entry
        for name, entry in current["entries"].items()
        if previous["entries"].get(name) != entry
    }
    removed = sorted(set(previous["entries"]) - set(current["entries"]))
    manifest = {
        "version": 1,
        "createdAt": datetime.now(UTC).isoformat(),
        "snapshot": current,
        "changed": sorted(changed),
        "previous": {name: previous["entries"].get(name) for name in changed},
        "removed": removed,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".partial-" + uuid.uuid4().hex)
    try:
        with zipfile.ZipFile(
            temporary, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=3
        ) as archive:
            archive.writestr(
                MANIFEST, json.dumps(manifest, ensure_ascii=True, sort_keys=True)
            )
            archive.write(Path(__file__), TOOL)
            for name, entry in changed.items():
                path = safe_path(root, name)
                if entry["kind"] == "file":
                    archive.write(path, "files/" + name)
                    if describe(path, root) != entry:
                        fail(
                            f"Arquivo mudou durante a cópia: {name}. Pare gravações e repita."
                        )
        with zipfile.ZipFile(temporary) as archive:
            for name, entry in changed.items():
                if entry["kind"] == "file":
                    with archive.open("files/" + name) as stream:
                        if (
                            hashlib.file_digest(stream, "sha256").hexdigest()
                            != entry["sha256"]
                        ):
                            fail(f"Hash divergente no ZIP: {name}")
        os.replace(temporary, output)
        write_json(control, current)
    finally:
        temporary.unlink(missing_ok=True)
    total = sum(entry["size"] for entry in changed.values())
    print(
        f"Pacote: {output}\nArquivos novos/alterados: {len(changed)}; "
        f"dados: {total / 1024**2:.1f} MiB"
    )
    print(f"Removidos na origem: {len(removed)} (não serão apagados no destino).")
    print(
        "Guarde e aplique os pacotes em ordem. "
        "Segredos excluídos por nome, salvo --include-sensitive."
    )


def restore(root: Path, source: Path, overwrite: bool, dry_run: bool) -> None:
    control = state_path(root)
    with zipfile.ZipFile(source) as archive:
        manifest = json.loads(archive.read(MANIFEST))
        current = manifest.get("snapshot", {})
        if manifest.get("version") != 1 or current.get("version") != 1:
            fail("Formato de pacote incompatível")
        entries = current["entries"]
        changed = set(manifest["changed"])
        if not changed.issubset(entries) or len(archive.namelist()) != len(
            set(archive.namelist())
        ):
            fail("Manifesto ou ZIP contém entradas inconsistentes")
        tracked = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"], check=True, capture_output=True
        ).stdout.split(b"\0")
        tracked_names = {os.fsdecode(name) for name in tracked if name}
        pending = []
        conflicts = []
        for name, entry in entries.items():
            path = safe_path(root, name)
            if name in tracked_names:
                fail(
                    f"O destino já versiona este arquivo; ele não será sobrescrito: {name}"
                )
            if entry["kind"] == "symlink":
                target = entry["target"]
                resolved = (path.parent / target).resolve()
                if Path(target).is_absolute() or not resolved.is_relative_to(root):
                    fail(f"Link inseguro: {name}")
                if resolved.is_relative_to(root / ".git") or resolved.is_relative_to(
                    root / ".private/transfer"
                ):
                    fail(f"Link para pasta reservada: {name}")
                if any(other.startswith(name + "/") for other in entries):
                    fail(f"Link usado como diretório no pacote: {name}")
                if hashlib.sha256(os.fsencode(target)).hexdigest() != entry["sha256"]:
                    fail(f"Hash de link divergente: {name}")
            elif entry["kind"] != "file":
                fail(f"Tipo de arquivo inválido: {name}")
            if path.is_dir() and not path.is_symlink():
                fail(f"Diretório impede a restauração: {name}")
            existing = describe(path, root)
            if existing == entry:
                continue
            if name not in changed:
                fail(
                    f"Falta a versão esperada de {name}. Aplique o pacote anterior ou gere --full."
                )
            if path.exists() or path.is_symlink():
                if existing != manifest["previous"].get(name) and not overwrite:
                    conflicts.append(name)
            pending.append((name, entry))
        if conflicts:
            fail(
                "Arquivos diferentes no destino: "
                + ", ".join(conflicts[:8])
                + ". Confira-os ou use --overwrite (guarda backup antes de substituir)."
            )
        for name in changed:
            entry = entries[name]
            if entry["kind"] == "file":
                with archive.open("files/" + name) as stream:
                    actual = hashlib.file_digest(stream, "sha256").hexdigest()
                if actual != entry["sha256"]:
                    fail(f"Hash divergente no pacote: {name}")
        print(f"Arquivos a restaurar: {len(pending)}; exclusões automáticas: nenhuma.")
        if dry_run:
            print("Simulação: nenhum arquivo alterado.")
            return
        backup = (
            root
            / ".private/transfer/backups"
            / (
                datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
                + "-"
                + uuid.uuid4().hex[:8]
            )
        )
        for name, entry in pending:
            path = safe_path(root, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() or path.is_symlink():
                saved = backup / name
                saved.parent.mkdir(parents=True, exist_ok=True)
                if path.is_dir() and not path.is_symlink():
                    fail(f"Diretório impede a restauração: {name}")
                shutil.copy2(path, saved, follow_symlinks=False)
            if entry["kind"] == "symlink":
                temporary = path.with_name(path.name + ".transfer-" + uuid.uuid4().hex)
                temporary.symlink_to(entry["target"])
            else:
                with tempfile.NamedTemporaryFile(
                    dir=path.parent, delete=False
                ) as stream:
                    temporary = Path(stream.name)
                    with archive.open("files/" + name) as data:
                        shutil.copyfileobj(data, stream)
                temporary.chmod(entry["mode"] & 0o777)
            os.replace(temporary, path)
        write_json(control, current)
        print(
            "Restauração concluída. Backups: " + str(backup)
            if backup.exists()
            else "Restauração concluída."
        )


def main() -> None:
    os.umask(0o077)
    parser = argparse.ArgumentParser(
        description="Transfere somente arquivos ignorados pelo Git, sem caches."
    )
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest="command", required=True)
    pack = commands.add_parser(
        "pack", help="Cria pacote incremental; o primeiro é completo"
    )
    pack.add_argument("output", type=Path)
    pack.add_argument(
        "--full",
        action="store_true",
        help="Inclui novamente todos os arquivos elegíveis",
    )
    pack.add_argument(
        "--base", type=Path, help="Compara com manifesto obtido no outro PC"
    )
    pack.add_argument(
        "--include-sensitive",
        action="store_true",
        help="Inclui credenciais; ZIP não é criptografado",
    )
    unpack = commands.add_parser(
        "restore", help="Confere hashes e restaura sem apagar arquivos"
    )
    unpack.add_argument("archive", type=Path)
    unpack.add_argument("--dry-run", action="store_true")
    unpack.add_argument(
        "--overwrite",
        action="store_true",
        help="Substitui conflitos mantendo backup local",
    )
    inventory = commands.add_parser(
        "manifest",
        help="Gera manifesto do PC de destino para evitar um primeiro ZIP completo",
    )
    inventory.add_argument("output", type=Path)
    inventory.add_argument("--include-sensitive", action="store_true")
    args = parser.parse_args()
    root = Path(
        subprocess.run(
            ["git", "-C", str(args.repo.expanduser()), "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    ).resolve()
    if args.command == "pack":
        export(root, args.output, args.full, args.include_sensitive, args.base)
    elif args.command == "restore":
        restore(root, args.archive, args.overwrite, args.dry_run)
    else:
        output = args.output.expanduser().resolve()
        if output.is_relative_to(root):
            fail("Salve o manifesto fora do projeto")
        write_json(output, snapshot(root, args.include_sensitive))
        print(f"Manifesto criado: {output}")


if __name__ == "__main__":
    try:
        main()
    except (
        ValueError,
        OSError,
        KeyError,
        zipfile.BadZipFile,
        subprocess.CalledProcessError,
    ) as error:
        print(f"ERRO: {error}", file=sys.stderr)
        raise SystemExit(1) from None
