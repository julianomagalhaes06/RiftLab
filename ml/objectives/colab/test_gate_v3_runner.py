"""Verificações do protocolo com fixtures fictícias, sem modelos nem corpus reservado."""

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

HERE = Path(__file__).resolve().parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = load("evaluate_gate_v3")
builder = load("build_gate_v3_notebook")


class ExecutionJournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.journal = self.root / "journal"
        self.journal.mkdir()
        self.output = self.journal / "artifacts/fixture"

    def produce(self, command, **kwargs):
        self.output.mkdir(parents=True, exist_ok=True)
        (self.output / "summary.json").write_text('{"n": 1}')
        (self.output / "predictions.jsonl").write_text('{"fixture": true}\n')
        return subprocess.CompletedProcess(command, 0)

    def test_completed_run_is_reused_and_never_executed_twice(self):
        execute = Mock(side_effect=self.produce)
        runner.run_once(self.root, self.journal, "fixture", ["fake"], execute=execute)
        runner.run_once(self.root, self.journal, "fixture", ["fake"], execute=execute)
        self.assertEqual(execute.call_count, 1)

    def test_tampering_with_results_blocks_a_rerun(self):
        runner.run_once(
            self.root, self.journal, "fixture", ["fake"], execute=self.produce
        )
        (self.output / "summary.json").write_text('{"n": 2}')
        execute = Mock()
        with self.assertRaisesRegex(RuntimeError, "Resultados alterados"):
            runner.run_once(
                self.root, self.journal, "fixture", ["fake"], execute=execute
            )
        execute.assert_not_called()

    def test_interrupted_run_blocks_automatic_retry(self):
        (self.journal / "fixture.json").write_text(
            '{"status": "started", "attempt": 1}'
        )
        execute = Mock()
        with self.assertRaisesRegex(RuntimeError, "revisão técnica"):
            runner.run_once(
                self.root, self.journal, "fixture", ["fake"], execute=execute
            )
        execute.assert_not_called()

    def test_metrics_written_before_failure_are_recovered_without_rerun(self):
        def failure(command, **kwargs):
            self.produce(command)
            return subprocess.CompletedProcess(command, 1)

        with self.assertRaises(RuntimeError):
            runner.run_once(
                self.root, self.journal, "fixture", ["fake"], execute=failure
            )
        execute = Mock()
        runner.run_once(
            self.root, self.journal, "fixture", ["fake"], retry=True, execute=execute
        )
        execute.assert_not_called()
        state = json.loads((self.journal / "fixture.json").read_text())
        self.assertTrue(state["recoveredWithoutRerun"])

    def test_technical_failure_requires_explicit_retry_and_preserves_history(self):
        execute = Mock(return_value=subprocess.CompletedProcess(["fake"], 1))
        with self.assertRaises(RuntimeError):
            runner.run_once(
                self.root, self.journal, "fixture", ["fake"], execute=execute
            )
        with self.assertRaises(RuntimeError):
            runner.run_once(
                self.root, self.journal, "fixture", ["fake"], execute=execute
            )
        self.assertEqual(execute.call_count, 1)
        runner.run_once(
            self.root,
            self.journal,
            "fixture",
            ["fake"],
            retry=True,
            execute=self.produce,
        )
        self.assertTrue((self.journal / "fixture-attempt-1.json").exists())
        self.assertEqual(
            json.loads((self.journal / "fixture.json").read_text())["attempt"], 2
        )

    def test_existing_metrics_without_journal_never_trigger_execution(self):
        self.produce(["fake"])
        execute = Mock()
        with self.assertRaisesRegex(RuntimeError, "sem proveniência"):
            runner.run_once(
                self.root, self.journal, "fixture", ["fake"], execute=execute
            )
        execute.assert_not_called()


class GateAndNotebookTests(unittest.TestCase):
    def test_registration_requires_green_ci_on_the_exact_commit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / runner.PROOF_PATH).parent.mkdir()
            (root / runner.PROOF_PATH).write_text(
                json.dumps(
                    {
                        "commit": "b" * 40,
                        "ciRun": 1,
                        "ciStatus": "completed",
                        "ciConclusion": "success",
                        "ciName": "CI",
                    }
                )
            )
            with self.assertRaisesRegex(RuntimeError, "commit de registro"):
                runner.check_registration(root, "a" * 40, 1)

    def test_registration_rejects_unpublished_gate_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            gate = root / runner.GATE_PATH
            gate.parent.mkdir()
            gate.write_text("local changed")
            (root / runner.PROOF_PATH).write_text(
                json.dumps(
                    {
                        "commit": "a" * 40,
                        "ciRun": 1,
                        "ciStatus": "completed",
                        "ciConclusion": "success",
                        "ciName": "CI",
                        "gateSha256": "0" * 64,
                    }
                )
            )
            with self.assertRaisesRegex(RuntimeError, "registro publicado"):
                runner.check_registration(root, "a" * 40, 1)

    def test_packager_refuses_to_build_before_ci_succeeds(self):
        ci = {
            "head_sha": "a" * 40,
            "status": "in_progress",
            "conclusion": None,
            "name": "CI",
        }
        invoke = Mock(return_value=json.dumps(ci))
        with self.assertRaisesRegex(RuntimeError, "antes de empacotar"):
            builder.registration_proof(Path("unused"), "a" * 40, 1, invoke=invoke)
        self.assertEqual(invoke.call_count, 1)

    def test_packager_verifies_published_gate_without_exporting_credentials(self):
        import base64

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            gate = root / builder.GATE_PATH
            gate.parent.mkdir()
            gate.write_bytes(b"fixture registered gate")
            ci = {
                "head_sha": "a" * 40,
                "status": "completed",
                "conclusion": "success",
                "name": "CI",
            }
            remote = {
                "encoding": "base64",
                "content": base64.b64encode(gate.read_bytes()).decode(),
            }
            invoke = Mock(side_effect=[json.dumps(ci), json.dumps(remote)])
            proof = builder.registration_proof(root, "a" * 40, 1, invoke=invoke)
            (root / runner.PROOF_PATH).write_text(json.dumps(proof))
            self.assertEqual(runner.check_registration(root, "a" * 40, 1), proof)
            self.assertEqual(invoke.call_count, 2)
            self.assertNotIn("token", proof)

    def test_comparison_includes_latency_and_does_not_silently_promote_lora(self):
        with tempfile.TemporaryDirectory() as temp:
            journal = Path(temp)
            for version, run, _ in runner.RUNS:
                output = journal / "artifacts" / run
                output.mkdir(parents=True)
                summary = {
                    "n": 1,
                    "corpusSha256": "fixture",
                    "split": "test",
                    "perClass": {"NEEDS_INPUT": {"recall": 1.0}},
                    "jsonValidRate": 1.0,
                    "macroF1": 1.0,
                    "latencyP95S": 16.0 if version.startswith("rules") else 1.0,
                }
                (output / "summary.json").write_text(json.dumps(summary))
                (output / "predictions.jsonl").write_text(
                    json.dumps(
                        {
                            "id": "fixture",
                            "pred": "NEEDS_INPUT",
                            "label": "NEEDS_INPUT",
                            "category": "injection",
                        }
                    )
                    + "\n"
                )
            gate = {
                "gate": {
                    "reference": "rules_v1",
                    "thresholds": {
                        "jsonValidRate": 0.95,
                        "macroF1": 0.90,
                        "maxNeedsInputRecallDrop": 0.05,
                        "maxLatencyP95Seconds": 15.0,
                    },
                },
                "corpus": {"testSize": 1, "testSha256": "fixture"},
            }
            result = runner.collect_results(journal, gate)
            self.assertFalse(result["results"][1]["approved"])
            self.assertTrue(result["results"][2]["approved"])
            self.assertEqual(result["decision"]["defaultParserVersion"], "rules_v1")
            self.assertTrue(result["decision"]["requiresProviderDecision"])

    def test_hash_mismatch_stops_before_any_evaluator(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "fixture").write_text("changed")
            with self.assertRaisesRegex(RuntimeError, "hash divergente"):
                runner.check_files(root, {"fixture": "0" * 64})

    def test_notebook_cells_compile_and_contain_no_executed_outputs(self):
        notebook = builder.build_notebook(
            "a" * 40, 1, "b" * 64, {"fixture": "c" * 64}, "d" * 64
        )
        for index, cell in enumerate(notebook["cells"]):
            if cell["cell_type"] == "code":
                compile("".join(cell["source"]), f"cell-{index}", "exec")
                self.assertEqual(cell["outputs"], [])
                self.assertIsNone(cell["execution_count"])


if __name__ == "__main__":
    unittest.main()
