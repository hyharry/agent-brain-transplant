from __future__ import annotations
import argparse
from pathlib import Path
from .core import backup, restore_public, apply_secrets, PROFILES

def main() -> int:
    p = argparse.ArgumentParser(prog="agent-brain-transplant")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("backup")
    b.add_argument("--profile", choices=sorted(PROFILES), required=True)
    b.add_argument("--source-root", required=True)
    b.add_argument("--out-dir", required=True)
    b.add_argument("--agent-name")
    b.add_argument("--agent-path")
    b.add_argument("--dry-run", action="store_true")
    b.add_argument("--force", action="store_true")

    r = sub.add_parser("restore-public")
    r.add_argument("--bundle-dir", required=True)
    r.add_argument("--target-root", required=True)
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--force", action="store_true")

    s = sub.add_parser("apply-secrets")
    s.add_argument("--secrets-file", required=True)
    s.add_argument("--target-root", required=True)
    s.add_argument("--dry-run", action="store_true")

    args = p.parse_args()
    if args.cmd == "backup":
        files = backup(Path(args.source_root).expanduser(), args.profile, Path(args.out_dir), args.agent_name, args.agent_path, args.dry_run, args.force)
        print(f"Planned {len(files)} files" if args.dry_run else f"Backed up {len(files)} files")
    elif args.cmd == "restore-public":
        restore_public(Path(args.bundle_dir), Path(args.target_root), args.dry_run, args.force)
        print("Restore-public plan done" if args.dry_run else "Restore-public done")
    elif args.cmd == "apply-secrets":
        apply_secrets(Path(args.target_root), Path(args.secrets_file), args.dry_run)
        print("Apply-secrets plan done" if args.dry_run else "Apply-secrets done")
    return 0
