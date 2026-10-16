from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class RepositoryIgnoreTests(unittest.TestCase):
    def test_portable_assets_and_private_exclusions(self):
        source = Path(__file__).resolve().parents[2] / ".gitignore"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            shutil.copyfile(source, root / ".gitignore")
            expected = {
                "docs/requirements.md": False,
                "docs/aps/prototipo-final/index.html": False,
                ".private/artifacts/model/model.cbm": False,
                ".private/colab/CC-701/train_colab.py": False,
                ".private/colab/CC-703/riftlab-cc703-colab.zip": True,
                ".private/colab/CC-703/riftlab-cc703-resultados.zip": False,
                "artifacts/objectives/model/adapter_model.safetensors": False,
                ".env": True,
                ".env.production": True,
                ".env.example": False,
                ".private/riot-account.json": True,
                ".private/colab/CC-701/riot-account.json": True,
                ".private/colab/CC-701/.env": True,
                ".private/colab/CC-703/bundle-map.json": True,
                ".private/colab/CC-703/riftlab-cc703-colab/data/dataset.json": True,
                ".private/collection/raw.json": True,
                ".private/dataset/rows.json": True,
                ".private/validation/report.md": True,
                ".private/STATUS.md": True,
                "data/raw/match.json": True,
                ".aws": True,
                "local.pem": True,
                "local.p12": True,
                "frontend/node_modules/package/index.js": True,
                "backend/.venv/lib/package.py": True,
                "docs/cached-page.html": True,
                "docs/cached-page_files/script.js": True,
                "artifacts/objectives/logs/train.log": True,
            }
            for name, ignored in expected.items():
                with self.subTest(name=name):
                    result = subprocess.run(["git", "-C", str(root), "check-ignore", "--no-index", "-q", "--", name])
                    self.assertIn(result.returncode, (0, 1))
                    self.assertEqual(result.returncode == 0, ignored)


if __name__ == "__main__":
    unittest.main()
