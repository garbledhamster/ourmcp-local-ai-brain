from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import install

ROOT = Path(__file__).resolve().parents[1]
FAKE_DISTILL = ROOT / "tests" / "fake_distill.py"


class LocalAiBrainCliTests(unittest.TestCase):
    def run_brain(self, *args: str, home: Path, check: bool = True):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT)
        env["LOCAL_AI_BRAIN_HOME"] = str(home)
        env["LOCAL_AI_BRAIN_DISTILL_CMD_JSON"] = json.dumps([sys.executable, str(FAKE_DISTILL)])
        proc = subprocess.run(
            [sys.executable, "-m", "local_ai_brain", *args],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
        )
        if check and proc.returncode != 0:
            self.fail(f"command failed: {args}\nstdout={proc.stdout}\nstderr={proc.stderr}")
        return proc

    def test_record_search_context_and_idempotent_reingest(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self.run_brain("init", home=home)
            payload = {
                "repo_path": "C:/Repo/example",
                "target_surface": "Settings",
                "ticket_title": "Fix mobile overflow",
                "status": "open",
                "summary": "Settings cards overflow on mobile.",
                "tags": ["mobile", "settings"],
                "related_files": ["assets/css/settings.css"],
                "body": "Fix assets/css/settings.css. Contact test@example.com should be scrubbed.",
            }
            payload_path = home / "payload.json"
            payload_path.write_text(json.dumps(payload), encoding="utf-8")
            first = self.run_brain("record-ticket", "--json-file", str(payload_path), home=home)
            second = self.run_brain("record-ticket", "--json-file", str(payload_path), home=home)
            self.assertIn('"ok": true', first.stdout)
            self.assertIn('"ok": true', second.stdout)
            search = self.run_brain("search", "--repo", "C:/Repo/example", "--query", "mobile overflow", home=home)
            rows = json.loads(search.stdout)
            self.assertEqual(1, len(rows))
            self.assertEqual("Fix mobile overflow", rows[0]["ticket_title"])
            self.assertIn("assets/css/settings.css", rows[0]["related_files"])
            scrubbed = Path(rows[0]["scrubbed_path"]).read_text(encoding="utf-8")
            self.assertIn("[REDACTED:email]", scrubbed)
            context = self.run_brain("context-pack", "--repo", "C:/Repo/example", "--surface", "Settings", "--query", "overflow", home=home)
            self.assertIn("Local AI Brain Context Pack", context.stdout)
            self.assertIn("Fix mobile overflow", context.stdout)
            con = sqlite3.connect(home / "brain.db")
            try:
                events = con.execute("SELECT event_type, summary FROM events ORDER BY id").fetchall()
            finally:
                con.close()
            event_types = [row[0] for row in events]
            self.assertIn("brain_search", event_types)
            self.assertIn("brain_context_pack", event_types)

    def test_health_and_what_if_install_for_macos(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            health = self.run_brain("health", home=home)
            self.assertIn("sqlite_fts5: True", health.stdout)
            self.assertIn("'ok': True", health.stdout)
            what_if = self.run_brain("install", "--what-if", "--platform", "macos", home=home)
            self.assertIn("WhatIf install for macos", what_if.stdout)
            self.assertIn("WOULD initialize SQLite schema", what_if.stdout)

    def test_distill_failure_blocks_capture(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            env = os.environ.copy()
            env["PYTHONPATH"] = str(ROOT)
            env["LOCAL_AI_BRAIN_HOME"] = str(home)
            env["LOCAL_AI_BRAIN_DISTILL_CMD_JSON"] = json.dumps([sys.executable, str(home / "missing.py")])
            payload_path = home / "payload.json"
            payload_path.write_text(json.dumps({"summary": "will fail"}), encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, "-m", "local_ai_brain", "record-artifact", "--json-file", str(payload_path)],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(0, proc.returncode)
            self.assertIn("distill failed", proc.stderr)

    def test_mcp_server_initialize_tools_and_context_pack(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            env = os.environ.copy()
            env["PYTHONPATH"] = str(ROOT)
            env["LOCAL_AI_BRAIN_HOME"] = str(home)
            env["LOCAL_AI_BRAIN_DISTILL_CMD_JSON"] = json.dumps([sys.executable, str(FAKE_DISTILL)])
            proc = subprocess.Popen(
                [sys.executable, "-m", "local_ai_brain.mcp_server"],
                cwd=ROOT,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                init = mcp_request(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
                self.assertEqual("local-ai-brain", init["result"]["serverInfo"]["name"])
                tools = mcp_request(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
                self.assertIn("context_pack", [tool["name"] for tool in tools["result"]["tools"]])
                context = mcp_request(
                    proc,
                    {
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "tools/call",
                        "params": {"name": "context_pack", "arguments": {"repo": "C:/Repo/example", "query": "none", "limit": 1}},
                    },
                )
                self.assertIn("Local AI Brain Context Pack", context["result"]["content"][0]["text"])
            finally:
                proc.kill()
                proc.communicate(timeout=5)


class LocalAiBrainInstallerTests(unittest.TestCase):
    def test_build_plan_for_macos(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = argparse_namespace(agents_home=tmp, platform="macos")
            plan = install.build_plan(args)
            self.assertEqual("macos", plan.platform_name)
            self.assertEqual(Path(tmp).resolve() / "tools" / "local-ai-brain", plan.destination_dir)
            self.assertTrue(str(plan.wrapper_command).endswith("local-ai-brain.sh"))

    def test_what_if_does_not_create_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = argparse_namespace(agents_home=tmp, platform="windows")
            plan = install.build_plan(args)
            result = install.run_plan(plan, what_if=True, force=False, no_doctor=True)
            self.assertEqual(0, result)
            self.assertFalse(plan.destination_dir.exists())

    def test_existing_destination_requires_force_when_not_in_place(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = argparse_namespace(agents_home=tmp, platform="windows")
            plan = install.build_plan(args)
            plan.destination_dir.mkdir(parents=True)
            with self.assertRaises(RuntimeError):
                install.copy_tool(plan, force=False)


def argparse_namespace(**kwargs):
    class Args:
        pass

    args = Args()
    args.agents_home = kwargs.get("agents_home", "")
    args.platform = kwargs.get("platform", "")
    return args


def mcp_request(proc: subprocess.Popen, payload: dict) -> dict:
    assert proc.stdin is not None
    assert proc.stdout is not None
    body = json.dumps(payload).encode("utf-8")
    proc.stdin.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
    proc.stdin.flush()
    headers: dict[str, str] = {}
    while True:
        line = proc.stdout.readline()
        if not line:
            raise AssertionError("MCP server closed stdout")
        text = line.decode("ascii").strip()
        if not text:
            break
        key, _, value = text.partition(":")
        headers[key.lower()] = value.strip()
    length = int(headers["content-length"])
    return json.loads(proc.stdout.read(length).decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
