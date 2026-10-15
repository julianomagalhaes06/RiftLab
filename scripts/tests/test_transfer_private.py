import importlib.util
import json
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "transfer_private.py"
SPEC = importlib.util.spec_from_file_location("transfer_private", SCRIPT)
transfer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(transfer)


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.source = self.repo("source")
        self.destination = self.repo("destination")

    def repo(self, name):
        root = self.folder / name
        root.mkdir()
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        (root / ".gitignore").write_text(
            ".private/\ndocs/\nAGENTS.md\n.env\nnode_modules/\n*.gpg\n"
        )
        (root / "tracked.txt").write_text("Código versionado")
        subprocess.run(
            ["git", "-C", str(root), "add", ".gitignore", "tracked.txt"], check=True
        )
        return root

    def put(self, root, name, content="texto"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def pack(self, name, full=False, sensitive=False, base=None):
        path = self.folder / name
        transfer.export(self.source, path, full, sensitive, base)
        return path

    def test_round_trip_ignored_only_and_internal_links(self):
        self.put(self.source, ".private/AGENTS.md", "Acordos")
        self.put(self.source, "docs/report.md", "Relatório")
        self.put(self.source, ".private/data.json", "{}")
        self.put(self.source, ".private/riot-account.json", "segredo")
        self.put(self.source, ".private/preview-real/subject-hash-secret", "segredo")
        self.put(self.source, ".env", "TOKEN=segredo")
        self.put(self.source, "node_modules/cache.js")
        self.put(self.source, ".private/.venv/cache")
        self.put(self.source, "old.gpg")
        self.put(self.source, "new-code.py")
        (self.source / "AGENTS.md").symlink_to(".private/AGENTS.md")
        (self.source / ".private/external-link").symlink_to("/etc/passwd")
        archive = self.pack("full.zip")
        transfer.restore(self.destination, archive, False, False)
        self.assertEqual((self.destination / "docs/report.md").read_text(), "Relatório")
        self.assertEqual((self.destination / "AGENTS.md").read_text(), "Acordos")
        self.assertEqual(
            (self.destination / "tracked.txt").read_text(), "Código versionado"
        )
        for name in (
            ".env",
            "node_modules",
            ".private/.venv",
            "old.gpg",
            "new-code.py",
            ".private/riot-account.json",
            ".private/external-link",
            ".private/preview-real/subject-hash-secret",
        ):
            self.assertFalse((self.destination / name).exists(), name)

    def test_incremental_alternating_pcs_and_backup(self):
        self.put(self.source, ".private/large.bin", "dados" * 1000)
        self.put(self.source, "docs/report.md", "primeiro")
        first = self.pack("first.zip")
        transfer.restore(self.destination, first, False, False)
        self.put(self.source, "docs/report.md", "segundo")
        second = self.pack("second.zip")
        with zipfile.ZipFile(second) as archive:
            manifest = json.loads(archive.read(transfer.MANIFEST))
            self.assertEqual(manifest["changed"], ["docs/report.md"])
            self.assertNotIn("files/.private/large.bin", archive.namelist())
        transfer.restore(self.destination, second, False, True)
        self.assertEqual((self.destination / "docs/report.md").read_text(), "primeiro")
        transfer.restore(self.destination, second, False, False)
        backups = list(
            (self.destination / ".private/transfer/backups").glob("*/docs/report.md")
        )
        self.assertEqual(backups[0].read_text(), "primeiro")
        self.put(self.destination, "docs/report.md", "volta")
        back = self.folder / "back.zip"
        transfer.export(self.destination, back, False, False, None)
        transfer.restore(self.source, back, False, False)
        self.assertEqual((self.source / "docs/report.md").read_text(), "volta")

    def test_conflict_refused_then_explicit_overwrite_preserves_backup(self):
        self.put(self.source, "docs/report.md", "origem")
        self.put(self.destination, "docs/report.md", "trabalho independente")
        archive = self.pack("conflict.zip")
        with self.assertRaisesRegex(ValueError, "Arquivos diferentes"):
            transfer.restore(self.destination, archive, False, False)
        self.assertEqual(
            (self.destination / "docs/report.md").read_text(), "trabalho independente"
        )
        transfer.restore(self.destination, archive, True, False)
        backup = next(
            (self.destination / ".private/transfer/backups").glob("*/docs/report.md")
        )
        self.assertEqual(backup.read_text(), "trabalho independente")

    def test_missing_baseline_fails_before_restoring_and_full_recovers(self):
        self.put(self.source, "docs/unchanged.md", "base")
        self.pack("lost.zip")
        self.put(self.source, "docs/new.md", "novo")
        delta = self.pack("delta.zip")
        with self.assertRaisesRegex(ValueError, "pacote anterior"):
            transfer.restore(self.destination, delta, False, False)
        self.assertFalse((self.destination / "docs/new.md").exists())
        full = self.pack("recovery.zip", full=True)
        transfer.restore(self.destination, full, False, False)
        self.assertEqual((self.destination / "docs/new.md").read_text(), "novo")

    def test_destination_manifest_avoids_resending_existing_data_and_deletions_are_retained(
        self,
    ):
        for root in (self.source, self.destination):
            self.put(root, ".private/large.bin", "igual")
        base = self.folder / "base.json"
        transfer.write_json(base, transfer.snapshot(self.destination, False))
        self.put(self.source, "docs/new.md", "novo")
        archive = self.pack("base.zip", base=base)
        with zipfile.ZipFile(archive) as bundle:
            self.assertNotIn("files/.private/large.bin", bundle.namelist())
        transfer.restore(self.destination, archive, False, False)
        (self.source / "docs/new.md").unlink()
        removed = self.pack("removed.zip")
        transfer.restore(self.destination, removed, False, False)
        self.assertTrue((self.destination / "docs/new.md").exists())

    def test_tampered_content_is_rejected_before_any_write(self):
        self.put(self.source, "docs/report.md", "original")
        archive = self.pack("original.zip")
        corrupt = self.folder / "corrupt.zip"
        with zipfile.ZipFile(archive) as original, zipfile.ZipFile(corrupt, "w") as out:
            for name in original.namelist():
                out.writestr(
                    name,
                    b"corrompido"
                    if name == "files/docs/report.md"
                    else original.read(name),
                )
        with self.assertRaisesRegex(ValueError, "Hash divergente"):
            transfer.restore(self.destination, corrupt, False, False)
        self.assertFalse((self.destination / "docs/report.md").exists())

    def test_unsafe_paths_and_tracked_files_are_never_overwritten(self):
        for name in (
            "../escape",
            "/absolute",
            ".git/config",
            ".private/transfer/state.json",
        ):
            with self.assertRaises(ValueError):
                transfer.safe_path(self.destination, name)
        (self.destination / "docs").symlink_to(self.source)
        with self.assertRaises(ValueError):
            transfer.safe_path(self.destination, "docs/report.md")
        (self.destination / "docs").unlink()
        self.put(self.source, "docs/report.md", "origem")
        archive = self.pack("tracked.zip")
        self.put(self.destination, "docs/report.md", "versionado no destino")
        (self.destination / ".gitignore").write_text(".private/\n")
        subprocess.run(
            ["git", "-C", str(self.destination), "add", "docs/report.md"], check=True
        )
        with self.assertRaisesRegex(ValueError, "já versiona"):
            transfer.restore(self.destination, archive, True, False)

    def test_sensitive_files_require_opt_in(self):
        self.put(self.source, ".env", "TOKEN=apenas-teste")
        archive = self.pack("sensitive.zip", sensitive=True)
        transfer.restore(self.destination, archive, False, False)
        self.assertEqual((self.destination / ".env").read_text(), "TOKEN=apenas-teste")

    def test_invalid_output_and_missing_base_do_not_advance_state(self):
        self.put(self.source, "docs/report.md", "relatório")
        with self.assertRaisesRegex(ValueError, "fora do projeto"):
            transfer.export(self.source, self.source / "inside.zip", False, False, None)
        with self.assertRaisesRegex(ValueError, "não existe"):
            self.pack("missing-base.zip", base=self.folder / "missing.json")
        self.assertFalse((self.source / transfer.STATE).exists())

    def test_embedded_script_bootstraps_another_pc(self):
        self.put(self.source, "docs/report.md", "portátil")
        archive = self.pack("portable.zip")
        script = self.folder / "portable-tool.py"
        with zipfile.ZipFile(archive) as bundle:
            script.write_bytes(bundle.read(transfer.TOOL))
        subprocess.run(
            [
                "python3",
                str(script),
                "--repo",
                str(self.destination),
                "restore",
                str(archive),
            ],
            check=True,
            capture_output=True,
        )
        self.assertEqual((self.destination / "docs/report.md").read_text(), "portátil")


if __name__ == "__main__":
    unittest.main()
