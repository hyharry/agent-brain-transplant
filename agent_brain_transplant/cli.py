from __future__ import annotations

import argparse
import json
from pathlib import Path

from .core import (
    PROFILES,
    apply_secrets,
    backup,
    build_backup_manifest,
    describe_manifest,
    restore_public,
)


def _add_common_source_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--profile", choices=sorted(PROFILES), required=True)
    parser.add_argument("--source-root", required=True, help="Root directory of the source OpenClaw/Hermes setup")
    parser.add_argument("--agent-name", help="Select a named agent/workspace under the source root")
    parser.add_argument("--agent-path", help="Select a specific agent/workspace path relative to the source root or absolute")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-brain-transplant")
    sub = parser.add_subparsers(dest="command", required=True)

    plan_parser = sub.add_parser("plan-backup", help="Show what would be included in a backup")
    _add_common_source_args(plan_parser)

    backup_parser = sub.add_parser("backup", help="Create public bundle + private secrets file")
    _add_common_source_args(backup_parser)
    backup_parser.add_argument("--out-dir", required=True)
    backup_parser.add_argument("--dry-run", action="store_true")
    backup_parser.add_argument("--force", action="store_true")

    restore_parser = sub.add_parser("restore-public", help="Copy the masked public bundle into a target root")
    restore_parser.add_argument("--bundle-dir", required=True)
    restore_parser.add_argument("--target-root", required=True)
    restore_parser.add_argument("--dry-run", action="store_true")
    restore_parser.add_argument("--force", action="store_true")

    secrets_parser = sub.add_parser("apply-secrets", help="Substitute private values into the restored target root")
    secrets_parser.add_argument("--secrets-file", required=True)
    secrets_parser.add_argument("--target-root", required=True)
    secrets_parser.add_argument("--dry-run", action="store_true")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "plan-backup":
        manifest = build_backup_manifest(
            Path(args.source_root),
            args.profile,
            agent_name=args.agent_name,
            agent_path=args.agent_path,
        )
        print(json.dumps(describe_manifest(manifest), indent=2))
        return 0

    if args.command == "backup":
        result = backup(
            Path(args.source_root),
            args.profile,
            Path(args.out_dir),
            agent_name=args.agent_name,
            agent_path=args.agent_path,
            dry_run=args.dry_run,
            force=args.force,
        )
        if args.dry_run:
            print(json.dumps(describe_manifest(result.manifest), indent=2))
        else:
            print(
                json.dumps(
                    {
                        "out_dir": str(Path(args.out_dir).expanduser()),
                        "file_count": result.manifest.file_count,
                        "secret_count": len(result.secrets),
                    },
                    indent=2,
                )
            )
        return 0

    if args.command == "restore-public":
        plan = restore_public(
            Path(args.bundle_dir),
            Path(args.target_root),
            dry_run=args.dry_run,
            force=args.force,
        )
        print(json.dumps({"copied_files": plan.copied_files, "skipped_files": plan.skipped_files}, indent=2))
        return 0

    if args.command == "apply-secrets":
        plan = apply_secrets(
            Path(args.target_root),
            Path(args.secrets_file),
            dry_run=args.dry_run,
        )
        print(json.dumps({"changed_files": plan.changed_files}, indent=2))
        return 0

    parser.error("unknown command")
    return 2
