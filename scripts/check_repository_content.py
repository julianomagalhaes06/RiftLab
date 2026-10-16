from __future__ import annotations

import argparse
import io
import re
import subprocess
import zipfile
from pathlib import Path, PurePosixPath

MAX_FILE_SIZE = 100 * 1024**2
MAX_ARCHIVE_CONTENT = 2 * 1024**3
PATTERNS = {
    "RIOT_API_KEY": rb"RGAPI-[A-Za-z0-9-]{32,}",
    "AWS_ACCESS_KEY": rb"(?:AKIA|ASIA)[A-Z0-9]{16}",
    "GITHUB_TOKEN": rb"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,})",
    "PRIVATE_KEY": rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----",
    "GOOGLE_ACCESS_TOKEN": rb"ya29\.[A-Za-z0-9_-]{30,}",
    "HUGGING_FACE_TOKEN": rb"hf_[A-Za-z0-9]{20,}",
}
IDENTIFIERS = re.compile(
    rb'"(?:puuid|summonerId|summonerName|riotIdGameName|riotIdTagline|gameName|tagLine|subjectHash|subject_hash)"\s*:\s*"[^"\r\n]+"',
    re.IGNORECASE,
)
DATA_SUFFIXES = {".json", ".jsonl", ".ipynb", ".html"}


def forbidden_name(name: str) -> bool:
    path = Path(name)
    return (
        path.name.startswith(".env") and path.name != ".env.example"
        or path.name.startswith("riot-account")
        or path.name.startswith("bundle-map")
        or path.name in {"credentials", "id_rsa", "id_ed25519", "id_ecdsa"}
        or ".aws" in path.parts
        or path.suffix.lower() in {".pem", ".key", ".p12", ".pfx"}
    )


def inspect_bytes(name: str, data: bytes, known_secrets: tuple[bytes, ...] = ()) -> set[str]:
    findings = {code for code, pattern in PATTERNS.items() if re.search(pattern, data)}
    if any(secret in data for secret in known_secrets):
        findings.add("LOCAL_SECRET")
    if Path(name).suffix.lower() in DATA_SUFFIXES and IDENTIFIERS.search(data):
        findings.add("PLAYER_IDENTIFIERS")
    if Path(name).suffix.lower() in DATA_SUFFIXES and re.search(rb'"(?:matchId|match_id|matchIds)"\s*:\s*(?:\[\s*)?"BR1_[0-9]+"', data):
        findings.add("ORIGINAL_MATCH_IDENTIFIERS")
    if forbidden_name(name):
        findings.add("SENSITIVE_FILENAME")
    return findings


def inspect_file(name: str, data: bytes, known_secrets: tuple[bytes, ...] = ()) -> set[str]:
    findings = inspect_bytes(name, data, known_secrets)
    if len(data) > MAX_FILE_SIZE:
        findings.add("GITHUB_FILE_SIZE_LIMIT")
    if Path(name).suffix.lower() != ".zip":
        return findings
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if sum(member.file_size for member in archive.infolist()) > MAX_ARCHIVE_CONTENT:
                return findings | {"ARCHIVE_TOO_LARGE_TO_AUDIT"}
            for member in archive.infolist():
                relative = PurePosixPath(member.filename)
                if relative.is_absolute() or ".." in relative.parts or "\\" in member.filename:
                    findings.add("ARCHIVE_UNSAFE_PATH")
                if member.is_dir():
                    continue
                if forbidden_name(member.filename):
                    findings.add("ARCHIVE_SENSITIVE_FILENAME")
                if Path(member.filename).suffix.lower() == ".zip":
                    findings.add("NESTED_ARCHIVE_REQUIRES_REVIEW")
                    continue
                with archive.open(member) as stream:
                    overlap = b""
                    while chunk := stream.read(4 * 1024**2):
                        content = overlap + chunk
                        findings.update(inspect_bytes(member.filename, content, known_secrets))
                        overlap = content[-4096:]
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError):
        findings.add("ARCHIVE_UNREADABLE")
    return findings


def local_secrets(root: Path) -> tuple[bytes, ...]:
    values = []
    env = root / ".env"
    if env.is_file():
        for line in env.read_text().splitlines():
            key, separator, value = line.partition("=")
            if separator and any(word in key.upper() for word in ("KEY", "TOKEN", "SECRET", "PASSWORD")):
                value = value.strip().strip("\"'")
                if len(value) >= 12:
                    values.append(value.encode())
    return tuple(values)


def worktree_entry(path: Path) -> tuple[str, bytes | None]:
    return str(path), None if path.stat().st_size > MAX_FILE_SIZE else path.read_bytes()


def staged_entry(root: Path, name: str) -> tuple[str, bytes | None]:
    size = int(subprocess.check_output(["git", "cat-file", "-s", f":{name}"], cwd=root))
    return name, None if size > MAX_FILE_SIZE else subprocess.check_output(["git", "show", f":{name}"], cwd=root)


def main() -> int:
    parser = argparse.ArgumentParser(description="Bloqueia segredos, dados pessoais e arquivos grandes; nunca imprime valores encontrados.")
    parser.add_argument("--paths", nargs="+", type=Path, help="Audita caminhos locais em vez do conteúdo staged.")
    args = parser.parse_args()
    root = Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip())
    secrets = local_secrets(root)
    failed = 0
    checked = 0
    if args.paths:
        paths = sorted({entry for path in args.paths for entry in (path.rglob("*") if path.is_dir() else [path]) if entry.is_file()})
        entries = (worktree_entry(path) for path in paths if not path.is_symlink())
    else:
        names = subprocess.check_output(["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"], cwd=root).decode().split("\0")
        entries = (staged_entry(root, name) for name in names if name)
    for name, data in entries:
        checked += 1
        findings = {"GITHUB_FILE_SIZE_LIMIT"} if data is None else inspect_file(name, data, secrets)
        if findings:
            failed += 1
            print(f"BLOQUEADO {name}: {', '.join(sorted(findings))}")
    print(f"Arquivos auditados: {checked}; bloqueados para revisão: {failed}. Nenhum valor sensível foi impresso.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
