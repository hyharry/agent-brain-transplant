import json
import tempfile
import unittest
from pathlib import Path

from agent_brain_transplant.core import (
    apply_secrets,
    backup,
    build_backup_manifest,
    discover_agent_paths,
    mask_text,
    restore_public,
)


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
        masked, changed = mask_text(text, "openclaw.json", secrets)
        self.assertTrue(changed)
        self.assertIn("__ABT_SECRET_", masked)
        self.assertEqual(len(secrets), 3)
        for meta in secrets.values():
            self.assertIn("value", meta)
            self.assertIn("path", meta)
            self.assertIn("key_hint", meta)

    def test_build_backup_manifest_includes_platform_agent_and_work(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "openclaw.json").write_text('token="abc"', encoding="utf-8")
            (root / "agents" / "a1").mkdir(parents=True)
            (root / "agents" / "a1" / "SOUL.md").write_text("hello", encoding="utf-8")
            (root / "memory").mkdir()
            (root / "memory" / "today.md").write_text("notes", encoding="utf-8")
            manifest = build_backup_manifest(root, "openclaw", agent_name="a1")
            self.assertEqual(manifest.profile, "openclaw")
            self.assertGreaterEqual(manifest.file_count, 3)
            self.assertGreaterEqual(manifest.categories["platform"], 1)
            self.assertGreaterEqual(manifest.categories["agent"], 1)
            self.assertGreaterEqual(manifest.categories["work"], 1)

    def test_backup_restore_and_apply_secrets_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "src"
            (root / "agents" / "a1").mkdir(parents=True)
            (root / "memory").mkdir(parents=True)
            (root / "openclaw.json").write_text('{"token": "123", "model": "x"}', encoding="utf-8")
            (root / "agents" / "a1" / "SOUL.md").write_text("password=abc", encoding="utf-8")
            (root / "memory" / "today.md").write_text("normal notes", encoding="utf-8")

            out = Path(td) / "out"
            result = backup(root, "openclaw", out, agent_name="a1")
            self.assertEqual(result.manifest.file_count, 3)
            self.assertTrue((out / "manifest.json").exists())
            self.assertTrue((out / "private_secrets.json").exists())

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

    def test_restore_public_dry_run_does_not_write(self):
        with tempfile.TemporaryDirectory() as td:
            bundle = Path(td) / "bundle"
            target = Path(td) / "target"
            (bundle / "agents").mkdir(parents=True)
            bundle.joinpath("agents", "x.txt").write_text("hi", encoding="utf-8")
            plan = restore_public(bundle, target, dry_run=True)
            self.assertIn("agents/x.txt", plan.copied_files)
            self.assertFalse(target.exists())

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


if __name__ == "__main__":
    unittest.main()
