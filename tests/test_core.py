import json
import tempfile
import unittest
from pathlib import Path

from agent_brain_transplant.core import mask_text, backup, restore_public, apply_secrets

class TestCore(unittest.TestCase):
    def test_mask_text(self):
        secrets = {}
        out = mask_text('api_key=abcd\npassword: xyz', secrets)
        self.assertIn('__ABT_SECRET_', out)
        self.assertEqual(len(secrets), 2)

    def test_backup_restore_apply(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / 'src'
            (root / 'agents' / 'a1').mkdir(parents=True)
            (root / 'openclaw.json').write_text('token=123', encoding='utf-8')
            (root / 'agents' / 'a1' / 'SOUL.md').write_text('password=abc', encoding='utf-8')
            out = Path(td) / 'out'
            files = backup(root, 'openclaw', out, 'a1', None, False, False)
            self.assertTrue(files)
            bundle = out / 'public_bundle'
            self.assertTrue((out / 'private_secrets.json').exists())
            target = Path(td) / 'target'
            restore_public(bundle, target, False, False)
            masked = (target / 'openclaw.json').read_text(encoding='utf-8')
            self.assertIn('__ABT_SECRET_', masked)
            apply_secrets(target, out / 'private_secrets.json', False)
            restored = (target / 'openclaw.json').read_text(encoding='utf-8')
            self.assertIn('123', restored)

    def test_restore_no_overwrite(self):
        with tempfile.TemporaryDirectory() as td:
            b = Path(td) / 'bundle'
            t = Path(td) / 'target'
            b.mkdir()
            t.mkdir()
            (b / 'f.txt').write_text('a', encoding='utf-8')
            (t / 'f.txt').write_text('b', encoding='utf-8')
            with self.assertRaises(FileExistsError):
                restore_public(b, t, False, False)

if __name__ == '__main__':
    unittest.main()
