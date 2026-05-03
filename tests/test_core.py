import json
import contextlib
import io
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from agent_brain_transplant.core import (
    PlannerError,
    _post_check_openclaw_json,
    apply_secrets,
    backup,
    build_backup_manifest,
    discover_agent_paths,
    get_agent_info,
    list_agents,
    mask_text,
    restore_public,
)
from agent_brain_transplant.cli import _format_agent_info, _format_agent_list, _format_restore_plan, main


class CoreTests(unittest.TestCase):
    def test_discover_agent_paths_by_name(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "agents" / "brainy").mkdir(parents=True)
            (root / "workspace-brainy").mkdir()
            found = discover_agent_paths(root, agent_name="brainy")
            self.assertEqual({p.name for p in found}, {"brainy", "workspace-brainy"})

    def test_mask_text_extracts_multiple_secret_styles(self):
        secrets = {}
        text = '{"api_key": "abc123", "safe": "ok"}\npassword: xyz\nchat_id=12345\n'
        masked, changed, notes = mask_text(text, "openclaw.json", secrets)
        self.assertTrue(changed)
        self.assertIn("__ABT_SECRET_", masked)
        self.assertEqual(len(secrets), 3)
        self.assertTrue(any("JSON parse failed" in note for note in notes))
        for meta in secrets.values():
            self.assertIn("value", meta)
            self.assertIn("path", meta)
            self.assertIn("key_hint", meta)

    def test_mask_text_extracts_messaging_ids_and_allowlists(self):
        secrets = {}
        text = "\n".join(
            [
                "FEISHU_OPEN_ID=ou_private",
                "TELEGRAM_USER_ID=123456",
                "telegram_group_id=-10098765",
                "id_allowlist=123,456,789",
                "allowed_user_ids: 42,84",
                "allowFrom: personal-source",
                "appId: cli_app_id",
            ]
        )
        masked, changed, notes = mask_text(text, ".env", secrets)
        self.assertTrue(changed)
        self.assertEqual(len(secrets), 7)
        self.assertEqual(notes, [])
        self.assertIn("__ABT_SECRET_", masked)
        for value in ("ou_private", "123456", "-10098765", "123,456,789", "42,84", "personal-source", "cli_app_id"):
            self.assertNotIn(value, masked)
        stored_values = {entry["value"] for entry in secrets.values()}
        self.assertIn("ou_private", stored_values)
        self.assertIn("-10098765", stored_values)
        self.assertIn("personal-source", stored_values)
        self.assertIn("cli_app_id", stored_values)

    def test_post_check_openclaw_json_prunes_channel_bindings_and_keeps_valid_json(self):
        text = json.dumps(
            {
                "runtime": {"model": "x"},
                "channel": "telegram",
                "plugins": {
                    "entries": {
                        "telegram": {"token": "abc", "chat_id": "123"},
                        "memory": {"enabled": True},
                    }
                },
                "agentBindings": [
                    {"agent": "main", "channel": "telegram", "accountId": "acc-1"},
                    {"agent": "worker", "channel": "discord", "threadId": "t-1"},
                ],
                "keep": {"safe": True},
            }
        )
        updated, changed, notes = _post_check_openclaw_json(text, ignore_channel=True)
        self.assertTrue(changed)
        self.assertTrue(notes)
        parsed = json.loads(updated)
        self.assertEqual(parsed["runtime"], {"model": "x"})
        self.assertEqual(parsed["keep"], {"safe": True})
        self.assertNotIn("channel", parsed)
        self.assertNotIn("telegram", parsed["plugins"]["entries"])
        self.assertEqual(parsed["plugins"]["entries"]["memory"], {"enabled": True})
        self.assertEqual(parsed["agentBindings"], [])

    def test_post_check_openclaw_json_leaves_invalid_json_unchanged_with_note(self):
        text = '{"plugins": { invalid }'
        updated, changed, notes = _post_check_openclaw_json(text, ignore_channel=True)
        self.assertEqual(updated, text)
        self.assertFalse(changed)
        self.assertTrue(any("JSON parse failed" in note for note in notes))

    def test_build_backup_manifest_includes_platform_agent_and_work(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "openclaw.json").write_text('token="abc"', encoding="utf-8")
            (root / "agents" / "a1").mkdir(parents=True)
            (root / "agents" / "a1" / "SOUL.md").write_text("hello", encoding="utf-8")
            (root / "workspace" / "main.md").parent.mkdir(parents=True)
            (root / "workspace" / "main.md").write_text("main workspace", encoding="utf-8")
            (root / "workspace-a1" / "agent.md").parent.mkdir(parents=True)
            (root / "workspace-a1" / "agent.md").write_text("agent workspace", encoding="utf-8")
            (root / "agents" / "a1" / "chat_session_1.json").write_text("{}", encoding="utf-8")
            (root / "agents" / "a1" / "sessions" / "current.md").parent.mkdir(parents=True)
            (root / "agents" / "a1" / "sessions" / "current.md").write_text("ephemeral", encoding="utf-8")
            (root / "agents" / "a1" / "chat_sessions" / "trace.log").parent.mkdir(parents=True)
            (root / "agents" / "a1" / "chat_sessions" / "trace.log").write_text("ephemeral", encoding="utf-8")
            (root / "memory" / "a1").mkdir(parents=True)
            (root / "memory" / "a1" / "today.md").write_text("notes", encoding="utf-8")
            (root / "memory" / "other").mkdir()
            (root / "memory" / "other" / "today.md").write_text("other notes", encoding="utf-8")
            (root / "memory" / "history").mkdir()
            (root / "memory" / "history" / "old-1.json").write_text("{}", encoding="utf-8")
            manifest = build_backup_manifest(root, "openclaw", agent_name="a1")
            self.assertEqual(manifest.profile, "openclaw")
            self.assertGreaterEqual(manifest.file_count, 3)
            self.assertGreaterEqual(manifest.categories["platform"], 1)
            self.assertGreaterEqual(manifest.categories["agent"], 1)
            self.assertGreaterEqual(manifest.categories["work"], 1)
            relatives = {item.relative for item in manifest.files}
            self.assertNotIn("agents/a1/chat_session_1.json", relatives)
            self.assertNotIn("agents/a1/sessions/current.md", relatives)
            self.assertNotIn("agents/a1/chat_sessions/trace.log", relatives)
            self.assertNotIn("memory/history/old-1.json", relatives)
            self.assertIn("memory/a1/today.md", relatives)
            self.assertNotIn("memory/other/today.md", relatives)
            self.assertIn("workspace-a1/agent.md", relatives)
            self.assertNotIn("workspace/main.md", relatives)
            self.assertFalse(any("session" in relative.lower() for relative in relatives))

    def test_backup_restore_and_apply_secrets_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "src"
            (root / "agents" / "a1").mkdir(parents=True)
            (root / "memory").mkdir(parents=True)
            (root / "openclaw.json").write_text('{"token": "123", "model": "x"}', encoding="utf-8")
            (root / ".env").write_text("API_KEY=env-secret\nSAFE_VALUE=ok\n", encoding="utf-8")
            (root / "agents" / "a1" / "SOUL.md").write_text("password=abc", encoding="utf-8")
            (root / "agents" / "a1" / "chat_session_1.json").write_text("{}", encoding="utf-8")
            (root / "agents" / "a1" / "sessions" / "current.md").parent.mkdir(parents=True)
            (root / "agents" / "a1" / "sessions" / "current.md").write_text("ephemeral", encoding="utf-8")
            (root / "memory" / "a1").mkdir(parents=True)
            (root / "memory" / "a1" / "today.md").write_text("normal notes", encoding="utf-8")
            (root / "memory" / "past_sessions").mkdir()
            (root / "memory" / "past_sessions" / "old-1.json").write_text("{}", encoding="utf-8")

            out = Path(td) / "out"
            result = backup(root, "openclaw", out, agent_name="a1")
            self.assertEqual(result.manifest.file_count, 4)
            self.assertTrue((out / "manifest.json").exists())
            self.assertTrue((out / "private_secrets.json").exists())
            self.assertIn(".env", {item.relative for item in result.manifest.files})
            masked_env = (out / "public_bundle" / ".env").read_text(encoding="utf-8")
            self.assertIn("__ABT_SECRET_", masked_env)
            self.assertNotIn("env-secret", masked_env)
            private_secrets = json.loads((out / "private_secrets.json").read_text(encoding="utf-8"))
            self.assertTrue(any(secret["path"] == ".env" and secret["value"] == "env-secret" for secret in private_secrets.values()))
            self.assertFalse((out / "public_bundle" / "agents" / "a1" / "chat_session_1.json").exists())
            self.assertFalse((out / "public_bundle" / "agents" / "a1" / "sessions" / "current.md").exists())
            self.assertFalse((out / "public_bundle" / "memory" / "past_sessions" / "old-1.json").exists())
            self.assertTrue((out / "public_bundle" / "memory" / "a1" / "today.md").exists())
            manifest_text = (out / "manifest.json").read_text(encoding="utf-8")
            self.assertNotIn("chat_session_1.json", manifest_text)
            self.assertNotIn("sessions/current.md", manifest_text)
            self.assertNotIn("past_sessions", manifest_text)

            target = Path(td) / "target"
            restore_plan = restore_public(out / "public_bundle", target)
            self.assertIn("openclaw.json", restore_plan.copied_files)
            masked = (target / "openclaw.json").read_text(encoding="utf-8")
            self.assertIn("__ABT_SECRET_", masked)

            apply_plan = apply_secrets(target, out / "private_secrets.json")
            self.assertIn("openclaw.json", apply_plan.changed_files)
            restored = (target / "openclaw.json").read_text(encoding="utf-8")
            self.assertIn('123', restored)
            soul = (target / "agents" / "a1" / "SOUL.md").read_text(encoding="utf-8")
            self.assertIn("password=abc", soul)
            env = (target / ".env").read_text(encoding="utf-8")
            self.assertIn("API_KEY=env-secret", env)

    def test_backup_manifest_compacts_deep_workspace_paths(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "src"
            (root / "agents" / "a1").mkdir(parents=True)
            (root / "agents" / "a1" / "SOUL.md").write_text("hello", encoding="utf-8")
            deep_file = root / "workspace-a1" / "src" / "pkg" / "deep" / "feature.py"
            deep_file.parent.mkdir(parents=True)
            deep_file.write_text("print('kept')", encoding="utf-8")

            out = Path(td) / "out"
            backup(root, "openclaw", out, agent_name="a1")

            self.assertTrue((out / "public_bundle" / "workspace-a1" / "src" / "pkg" / "deep" / "feature.py").exists())
            manifest_text = (out / "manifest.json").read_text(encoding="utf-8")
            self.assertIn("workspace-a1/src/pkg/...", manifest_text)
            self.assertNotIn("workspace-a1/src/pkg/deep/feature.py", manifest_text)

    def test_backup_config_only_includes_platform_and_model_settings(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "openclaw.json").write_text("{}", encoding="utf-8")
            (root / "model_settings.json").write_text("{}", encoding="utf-8")
            (root / "agents" / "a1").mkdir(parents=True)
            (root / "agents" / "a1" / "SOUL.md").write_text("hello", encoding="utf-8")
            (root / "memory" / "a1").mkdir(parents=True)
            (root / "memory" / "a1" / "today.md").write_text("notes", encoding="utf-8")
            (root / "workspace" / "project.md").parent.mkdir(parents=True)
            (root / "workspace" / "project.md").write_text("work", encoding="utf-8")

            manifest = build_backup_manifest(root, "openclaw", agent_name="a1", backup_mode="config")
            relatives = {item.relative for item in manifest.files}
            self.assertEqual(relatives, {"openclaw.json", "model_settings.json"})
            self.assertEqual(manifest.backup_mode, "config")
            self.assertFalse(manifest.ignore_channel)

    def test_backup_all_includes_all_agents_and_shared_work(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "openclaw.json").write_text("{}", encoding="utf-8")
            for name in ("a1", "a2"):
                (root / "agents" / name).mkdir(parents=True)
                (root / "agents" / name / "SOUL.md").write_text(name, encoding="utf-8")
                (root / "memory" / name).mkdir(parents=True)
                (root / "memory" / name / "today.md").write_text(name, encoding="utf-8")

            manifest = build_backup_manifest(root, "openclaw", backup_mode="all")
            relatives = {item.relative for item in manifest.files}
            self.assertIn("agents/a1/SOUL.md", relatives)
            self.assertIn("agents/a2/SOUL.md", relatives)
            self.assertIn("memory/a1/today.md", relatives)
            self.assertIn("memory/a2/today.md", relatives)

    def test_backup_slim_keeps_all_agents_markdown_workspaces_memory_and_skills(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "openclaw.json").write_text("{}", encoding="utf-8")
            (root / "agents" / "a1").mkdir(parents=True)
            (root / "agents" / "a1" / "SOUL.md").write_text("hello", encoding="utf-8")
            (root / "agents" / "a2").mkdir(parents=True)
            (root / "agents" / "a2" / "SOUL.md").write_text("hello", encoding="utf-8")
            (root / "workspace" / "main.md").parent.mkdir(parents=True)
            (root / "workspace" / "main.md").write_text("skip main", encoding="utf-8")
            (root / "workspace-a1" / "notes.md").parent.mkdir(parents=True)
            (root / "workspace-a1" / "notes.md").write_text("keep", encoding="utf-8")
            (root / "workspace-a1" / "data.bin").write_bytes(b"skip")
            (root / "workspace-a1" / "src" / "feature.py").parent.mkdir(parents=True)
            (root / "workspace-a1" / "src" / "feature.py").write_text("skip", encoding="utf-8")
            (root / "workspace-a1" / "src" / "deep.md").write_text("keep", encoding="utf-8")
            (root / "workspace-a2" / "plan.md").parent.mkdir(parents=True)
            (root / "workspace-a2" / "plan.md").write_text("keep", encoding="utf-8")
            (root / "workspace-a2" / "asset.png").write_bytes(b"skip")
            (root / "memory" / "a1").mkdir(parents=True)
            (root / "memory" / "a1" / "db.sqlite").write_bytes(b"keep")
            (root / "memory" / "a2").mkdir(parents=True)
            (root / "memory" / "a2" / "today.md").write_text("keep", encoding="utf-8")
            (root / "skills" / "a1" / "demo").mkdir(parents=True)
            (root / "skills" / "a1" / "demo" / "tool.py").write_text("keep", encoding="utf-8")
            (root / "skills" / "a2" / "demo").mkdir(parents=True)
            (root / "skills" / "a2" / "demo" / "SKILL.md").write_text("keep", encoding="utf-8")

            manifest = build_backup_manifest(root, "openclaw", backup_mode="slim")
            relatives = {item.relative for item in manifest.files}
            self.assertIn("openclaw.json", relatives)
            self.assertIn("agents/a1/SOUL.md", relatives)
            self.assertIn("agents/a2/SOUL.md", relatives)
            self.assertIn("workspace-a1/notes.md", relatives)
            self.assertIn("workspace-a1/src/deep.md", relatives)
            self.assertIn("workspace-a2/plan.md", relatives)
            self.assertIn("memory/a1/db.sqlite", relatives)
            self.assertIn("memory/a2/today.md", relatives)
            self.assertIn("skills/a1/demo/tool.py", relatives)
            self.assertIn("skills/a2/demo/SKILL.md", relatives)
            self.assertNotIn("workspace/main.md", relatives)
            self.assertNotIn("workspace-a1/data.bin", relatives)
            self.assertNotIn("workspace-a1/src/feature.py", relatives)
            self.assertNotIn("workspace-a2/asset.png", relatives)

    def test_ignore_channel_skips_channel_files_and_config(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "openclaw.json").write_text(
                json.dumps(
                    {
                        "channel": "telegram",
                        "plugins": {
                            "entries": {
                                "telegram": {"token": "abc", "chat_id": "123"},
                                "memory": {"enabled": True},
                            }
                        },
                        "agentBindings": [
                            {"agent": "main", "channel": "telegram", "accountId": "acc-1"}
                        ],
                        "runtime": {"model": "x"},
                    }
                ),
                encoding="utf-8",
            )
            (root / "telegram_settings.json").write_text('{"token": "abc"}', encoding="utf-8")
            (root / "agents" / "a1").mkdir(parents=True)
            (root / "agents" / "a1" / "SOUL.md").write_text("hello", encoding="utf-8")
            (root / "memory" / "a1").mkdir(parents=True)
            (root / "memory" / "a1" / "today.md").write_text("notes", encoding="utf-8")
            (root / "skills" / "a1" / "demo").mkdir(parents=True)
            (root / "skills" / "a1" / "demo" / "SKILL.md").write_text("keep", encoding="utf-8")
            (root / "channels" / "telegram" / "bindings.json").parent.mkdir(parents=True)
            (root / "channels" / "telegram" / "bindings.json").write_text("{}", encoding="utf-8")
            (root / "workspace-a1" / "notes.md").parent.mkdir(parents=True)
            (root / "workspace-a1" / "notes.md").write_text("keep", encoding="utf-8")
            (root / "workspace-a1" / "telegram" / "state.json").parent.mkdir(parents=True)
            (root / "workspace-a1" / "telegram" / "state.json").write_text("{}", encoding="utf-8")

            manifest = build_backup_manifest(root, "openclaw", backup_mode="all", ignore_channel=True)
            relatives = {item.relative for item in manifest.files}
            self.assertIn("openclaw.json", relatives)
            self.assertIn("agents/a1/SOUL.md", relatives)
            self.assertIn("memory/a1/today.md", relatives)
            self.assertIn("skills/a1/demo/SKILL.md", relatives)
            self.assertIn("workspace-a1/notes.md", relatives)
            self.assertNotIn("telegram_settings.json", relatives)
            self.assertNotIn("channels/telegram/bindings.json", relatives)
            self.assertNotIn("workspace-a1/telegram/state.json", relatives)
            self.assertTrue(any("--ignore-channel" in warning for warning in manifest.warnings))
            self.assertTrue(manifest.ignore_channel)

            out = Path(td) / "out"
            result = backup(root, "openclaw", out, backup_mode="all", ignore_channel=True)
            restored_openclaw = json.loads((out / "public_bundle" / "openclaw.json").read_text(encoding="utf-8"))
            self.assertEqual(restored_openclaw["runtime"], {"model": "x"})
            self.assertNotIn("telegram", restored_openclaw["plugins"]["entries"])
            self.assertEqual(restored_openclaw["plugins"]["entries"]["memory"], {"enabled": True})
            self.assertEqual(restored_openclaw["agentBindings"], [])
            self.assertNotIn("channel", restored_openclaw)
            self.assertTrue(any("openclaw.json" in warning for warning in result.manifest.warnings))

    def test_backup_exclude_filters_files_and_folders(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "openclaw.json").write_text("{}", encoding="utf-8")
            (root / "agents" / "a1").mkdir(parents=True)
            (root / "agents" / "a1" / "SOUL.md").write_text("hello", encoding="utf-8")
            (root / "memory" / "a1").mkdir(parents=True)
            (root / "memory" / "a1" / "today.md").write_text("notes", encoding="utf-8")
            (root / "workspace" / "build" / "out.txt").parent.mkdir(parents=True)
            (root / "workspace" / "build" / "out.txt").write_text("skip", encoding="utf-8")
            (root / "workspace-a1" / "agent_code" / "main.py").parent.mkdir(parents=True)
            (root / "workspace-a1" / "agent_code" / "main.py").write_text("skip", encoding="utf-8")
            (root / "workspace-a1" / "keep" / "main.py").parent.mkdir(parents=True)
            (root / "workspace-a1" / "keep" / "main.py").write_text("keep", encoding="utf-8")
            (root / "workspace" / "a1" / "agent_code" / "alt.py").parent.mkdir(parents=True)
            (root / "workspace" / "a1" / "agent_code" / "alt.py").write_text("skip", encoding="utf-8")

            manifest = build_backup_manifest(root, "openclaw", agent_name="a1", excludes=["memory/a1", "workspace/build", "workspace/agent_code"])
            relatives = {item.relative for item in manifest.files}
            self.assertNotIn("memory/a1/today.md", relatives)
            self.assertNotIn("workspace/build/out.txt", relatives)
            self.assertNotIn("workspace-a1/agent_code/main.py", relatives)
            self.assertNotIn("workspace/a1/agent_code/alt.py", relatives)
            self.assertIn("workspace-a1/keep/main.py", relatives)
            self.assertIn("agents/a1/SOUL.md", relatives)

    def test_all_agent_backup_commands_do_not_require_agent_selector(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "src"
            root.mkdir()
            (root / "openclaw.json").write_text("{}", encoding="utf-8")
            (root / "agents" / "a1").mkdir(parents=True)
            (root / "agents" / "a1" / "SOUL.md").write_text("hello", encoding="utf-8")
            out = Path(td) / "out"

            for command in ("backup-all", "backup-slim", "backup-config"):
                extra_args = ["--ignore-channel"] if command == "backup-all" else []
                argv = [
                    "agent-brain-transplant",
                    command,
                    "--profile",
                    "openclaw",
                    "--source-root",
                    str(root),
                    "--out-dir",
                    str(out / command),
                    "--dry-run",
                    *extra_args,
                ]
                with mock.patch("sys.argv", argv), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(main(), 0)

    def test_restore_public_dry_run_does_not_write(self):
        with tempfile.TemporaryDirectory() as td:
            bundle = Path(td) / "bundle"
            target = Path(td) / "target"
            (bundle / "agents").mkdir(parents=True)
            bundle.joinpath("agents", "x.txt").write_text("hi", encoding="utf-8")
            plan = restore_public(bundle, target, dry_run=True)
            self.assertIn("agents/x.txt", plan.copied_files)
            self.assertFalse(target.exists())

    def test_restore_output_is_compact(self):
        with tempfile.TemporaryDirectory() as td:
            bundle = Path(td) / "bundle"
            target = Path(td) / "target"
            deep_file = bundle / "workspace-a1" / "src" / "pkg" / "feature.py"
            deep_file.parent.mkdir(parents=True)
            deep_file.write_text("hi", encoding="utf-8")

            plan = restore_public(bundle, target)
            output = _format_restore_plan(plan)
            self.assertTrue((target / "workspace-a1" / "src" / "pkg" / "feature.py").exists())
            self.assertIn('"copied_count": 1', output)
            self.assertIn("workspace-a1/src/...", output)
            self.assertNotIn("workspace-a1/src/pkg/feature.py", output)

    def test_apply_secrets_dry_run_only_reports(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "target"
            target.mkdir()
            (target / "openclaw.json").write_text('token=__ABT_SECRET_token_x__', encoding="utf-8")
            secrets_file = Path(td) / "private_secrets.json"
            secrets_file.write_text(
                json.dumps({"__ABT_SECRET_token_x__": {"value": "real-token", "path": "openclaw.json", "key_hint": "token"}}),
                encoding="utf-8",
            )
            plan = apply_secrets(target, secrets_file, dry_run=True)
            self.assertEqual(plan.changed_files, ["openclaw.json"])
            self.assertIn("__ABT_SECRET_token_x__", (target / "openclaw.json").read_text(encoding="utf-8"))

    def test_restore_refuses_overwrite_without_force(self):
        with tempfile.TemporaryDirectory() as td:
            bundle = Path(td) / "bundle"
            target = Path(td) / "target"
            bundle.mkdir()
            target.mkdir()
            (bundle / "f.txt").write_text("a", encoding="utf-8")
            (target / "f.txt").write_text("b", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                restore_public(bundle, target, dry_run=False, force=False)

    def test_list_agents_and_agent_info(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            a1 = root / "agents" / "alpha"
            a1.mkdir(parents=True)
            workspace = root / "workspace-alpha"
            workspace.mkdir()
            (workspace / "project.txt").write_text("workspace data", encoding="utf-8")
            (a1 / "AGENTS.md").write_text("hello", encoding="utf-8")
            (a1 / "SOUL.md").write_text("hello", encoding="utf-8")
            (a1 / "skills" / "demo").mkdir(parents=True)
            (a1 / "skills" / "demo" / "SKILL.md").write_text("demo", encoding="utf-8")
            (a1 / "chat_session_1.json").write_text("{}", encoding="utf-8")
            (workspace / "past_sessions" / "old-1.json").parent.mkdir(parents=True)
            (workspace / "past_sessions" / "old-1.json").write_text("{}", encoding="utf-8")
            (workspace / "history" / "old-2.jsonl").parent.mkdir(parents=True)
            (workspace / "history" / "old-2.jsonl").write_text("{}", encoding="utf-8")
            (a1 / "STATE.md").write_text("status: in_progress", encoding="utf-8")

            items = list_agents(root, "openclaw")
            self.assertEqual(len(items), 1)
            info = items[0]
            self.assertEqual(info.name, "alpha")
            self.assertEqual(info.path, str(a1))
            self.assertEqual(info.workspace_path, str(workspace))
            self.assertEqual(info.state, "in_progress")
            self.assertEqual(info.session_count, 3)
            self.assertEqual(info.skill_count, 1)
            self.assertIn("demo", info.skills)
            self.assertIn("AGENTS.md", info.core_files)

            direct = get_agent_info(root, a1, "openclaw")
            self.assertEqual(direct.name, "alpha")
            self.assertGreater(direct.workspace_bytes, 0)

            list_output = _format_agent_list(items)
            self.assertIn("total_sessions", list_output)
            self.assertNotIn("session_count", list_output)

    def test_agent_info_workspace_listing_is_compact(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            agent = root / "agents" / "alpha"
            agent.mkdir(parents=True)
            workspace = root / "workspace-alpha"
            (workspace / "src" / "pkg" / "deep").mkdir(parents=True)
            (workspace / ".git" / "objects").mkdir(parents=True)
            (workspace / "src" / "top.txt").write_text("top", encoding="utf-8")
            (workspace / "src" / "pkg" / "deep" / "x.txt").write_text("deep", encoding="utf-8")

            output = _format_agent_info(get_agent_info(root, agent, "openclaw"))
            self.assertIn("workspace:", output)
            self.assertNotIn("core_files", output)
            self.assertIn(".", output)
            self.assertIn("src/", output)
            self.assertIn("src/pkg/", output)
            self.assertIn("src/top.txt", output)
            self.assertNotIn("src/pkg/deep/", output)
            self.assertNotIn(".git", output)
            self.assertNotIn("src/pkg/deep/x.txt", output)

    def test_list_agents_includes_plain_workspace_without_agent_settings(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "chat_session_1.json").write_text("{}", encoding="utf-8")
            (workspace / "notes.txt").write_text("main workspace", encoding="utf-8")

            items = list_agents(root, "openclaw")
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0].name, "main")
            self.assertEqual(items[0].path, str(workspace))
            self.assertEqual(items[0].workspace_path, str(workspace))
            self.assertEqual(items[0].session_count, 1)
            self.assertGreater(items[0].workspace_bytes, 0)

    def test_hermes_agent_alias_is_supported(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "hermes-agent.json").write_text('token="abc"', encoding="utf-8")
            (root / "SOUL.md").write_text("soul", encoding="utf-8")
            (root / ".env").write_text("TOKEN=hermes-env-secret\n", encoding="utf-8")
            manifest = build_backup_manifest(root, "hermes-agent")
            self.assertEqual(manifest.profile, "hermes-agent")
            self.assertGreaterEqual(manifest.categories["platform"], 1)
            relatives = {item.relative for item in manifest.files}
            self.assertIn("SOUL.md", relatives)
            self.assertIn(".env", relatives)
            self.assertTrue(any("memory" in warning for warning in manifest.warnings))

    def test_hermes_rejects_agent_selector(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "hermes-agent.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(PlannerError):
                build_backup_manifest(root, "hermes-agent", agent_name="worker")


if __name__ == "__main__":
    unittest.main()
