from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "check_repository_content.py"
SPEC = importlib.util.spec_from_file_location("check_repository_content", SCRIPT)
assert SPEC and SPEC.loader
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


class RepositoryContentTests(unittest.TestCase):
    def test_normal_engineering_data_is_allowed(self):
        data = json.dumps({"matchId": "m000001", "championId": 51, "accuracy": 0.5}).encode()
        self.assertEqual(audit.inspect_file("dataset.json", data), set())

    def test_api_key_is_blocked_without_echoing_it(self):
        key = ("RGAPI-" + "a" * 36).encode()
        self.assertEqual(audit.inspect_file("notes.md", key), {"RIOT_API_KEY"})

    def test_local_secret_is_found_even_without_a_known_prefix(self):
        value = b"private-synthetic-test-value"
        self.assertEqual(audit.inspect_file("notes.md", value, (value,)), {"LOCAL_SECRET"})

    def test_player_identifiers_are_blocked_in_structured_data(self):
        for key in ("puuid", "gameName", "tagLine", "subjectHash", "riotIdGameName"):
            with self.subTest(key=key):
                self.assertIn("PLAYER_IDENTIFIERS", audit.inspect_file("data.json", json.dumps({key: "synthetic-player"}).encode()))

    def test_original_match_ids_are_not_published(self):
        data = json.dumps({"matchId": "BR1_" + "123456789"}).encode()
        self.assertIn("ORIGINAL_MATCH_IDENTIFIERS", audit.inspect_file("rows.json", data))

    def test_sensitive_names_are_blocked(self):
        for name in (".env", "nested/.env.production", "riot-account.json", ".aws/credentials", "key.pem"):
            with self.subTest(name=name):
                self.assertTrue(audit.forbidden_name(name))
        self.assertFalse(audit.forbidden_name(".env.example"))

    def test_archive_contents_are_scanned(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("nested/.env", "EXAMPLE=not-a-real-secret")
            archive.writestr("dataset.json", json.dumps({"puuid": "synthetic-player"}))
        findings = audit.inspect_file("bundle.zip", stream.getvalue())
        self.assertIn("ARCHIVE_SENSITIVE_FILENAME", findings)
        self.assertIn("PLAYER_IDENTIFIERS", findings)

    def test_clean_archive_is_allowed(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("dataset.json", json.dumps({"matchId": "m000001", "itemId": 3031}))
        self.assertEqual(audit.inspect_file("bundle.zip", stream.getvalue()), set())

    def test_unreadable_archive_is_blocked(self):
        self.assertIn("ARCHIVE_UNREADABLE", audit.inspect_file("bundle.zip", b"broken"))

    def test_nested_archive_requires_review(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("nested.zip", b"needs-manual-review")
        self.assertIn("NESTED_ARCHIVE_REQUIRES_REVIEW", audit.inspect_file("bundle.zip", stream.getvalue()))

    def test_archive_path_traversal_is_blocked(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("../outside.txt", "not safe")
        self.assertIn("ARCHIVE_UNSAFE_PATH", audit.inspect_file("bundle.zip", stream.getvalue()))

    def test_staged_content_is_checked_instead_of_the_worktree(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            key = ("RGAPI-" + "b" * 36).encode()
            path = root / "notes.md"
            path.write_bytes(key)
            subprocess.run(["git", "-C", str(root), "add", "notes.md"], check=True)
            path.write_text("safe working tree")
            name, content = audit.staged_entry(root, "notes.md")
            self.assertIn("RIOT_API_KEY", audit.inspect_file(name, content))

    def test_large_files_can_be_rejected_before_reading(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "large.bin"
            path.write_bytes(b"xx")
            with patch.object(audit, "MAX_FILE_SIZE", 1):
                self.assertEqual(audit.worktree_entry(path), (str(path), None))


if __name__ == "__main__":
    unittest.main()
