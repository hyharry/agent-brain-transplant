from __future__ import annotations

import argparse
import json
from pathlib import Path

from .core import (
    CANONICAL_PROFILES,
    PROFILES,
    apply_secrets,
    backup,
    build_backup_manifest,
    describe_manifest,
    discover_agent_paths,
    get_agent_info,
    list_agents,
    restore_public,
)


def _profiles_help() -> str:
    return ", ".join(f"{name} ({'/'.join(profile.aliases)})" for name, profile in CANONICAL_PROFILES.items())


def _add_common_source_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--profile",
        choices=sorted(PROFILES),
        required=True,
        help=f"Source platform profile. Supported: {_profiles_help()}",
    )
    parser.add_argument(
        "--source-root",
        required=True,
        help="Root directory of the source OpenClaw/Hermes-agent setup to inspect or back up",
    )
    parser.add_argument(
        "--agent-name",
        help="Select a named agent/workspace under the source root, e.g. suyu_code_it",
    )
    parser.add_argument(
        "--agent-path",
        help="Select a specific agent/workspace path relative to the source root or absolute",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-brain-transplant",
        description=(
            "Back up and transplant OpenClaw or Hermes-agent brains in two phases: "
            "(1) masked public bundle, (2) private secrets substitution."
        ),
        epilog=(
            "Examples:\n"
            "  python3 -m agent_brain_transplant list-agents --profile openclaw --source-root ~/.openclaw\n"
            "  python3 -m agent_brain_transplant agent-info --profile openclaw --source-root ~/.openclaw --agent-name suyu_code_it\n"
            "  python3 -m agent_brain_transplant backup --profile hermes-agent --source-root ~/.hermes --agent-name worker --out-dir ./out/worker\n"
            "  python3 -m agent_brain_transplant restore-public --bundle-dir ./out/worker/public_bundle --target-root /tmp/new-agent\n"
            "  python3 -m agent_brain_transplant apply-secrets --secrets-file ./out/worker/private_secrets.json --target-root /tmp/new-agent"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    list_parser = sub.add_parser(
        "list-agents",
        help="List detected agent/workspace roots with compact stats",
        description="List detected agents/workspaces and report state, session count, storage size, and skills.",
    )
    list_parser.add_argument("--profile", choices=sorted(PROFILES), required=True, help=f"Profile to inspect. Supported: {_profiles_help()}")
    list_parser.add_argument("--source-root", required=True, help="Root directory to scan for agent/workspace folders")

    info_parser = sub.add_parser(
        "agent-info",
        help="Show detailed info for one agent/workspace",
        description="Show detailed info for one detected agent/workspace, including state, session count, storage size, and skills.",
    )
    _add_common_source_args(info_parser)

    plan_parser = sub.add_parser(
        "plan-backup",
        help="Show what would be included in a backup",
        description="Inspect the selected source and print a backup manifest without writing files.",
    )
    _add_common_source_args(plan_parser)

    backup_parser = sub.add_parser(
        "backup",
        help="Create public bundle + private secrets file",
        description=(
            "Create a masked public bundle plus a separate private_secrets.json file. "
            "Use --dry-run first if you want to preview the file plan."
        ),
    )
    _add_common_source_args(backup_parser)
    backup_parser.add_argument("--out-dir", required=True, help="Destination directory for public bundle, manifest, and private secrets file")
    backup_parser.add_argument("--dry-run", action="store_true", help="Print the backup manifest without writing output files")
    backup_parser.add_argument("--force", action="store_true", help="Allow replacing an existing output directory")

    restore_parser = sub.add_parser(
        "restore-public",
        help="Copy the masked public bundle into a target root",
        description="Restore the masked public bundle into a target root. Overwrites are refused unless --force is set.",
    )
    restore_parser.add_argument("--bundle-dir", required=True, help="Path to the public_bundle directory")
    restore_parser.add_argument("--target-root", required=True, help="Target root where the public bundle should be restored")
    restore_parser.add_argument("--dry-run", action="store_true", help="Show which files would be copied")
    restore_parser.add_argument("--force", action="store_true", help="Allow overwriting existing files in the target root")

    secrets_parser = sub.add_parser(
        "apply-secrets",
        help="Substitute private values into the restored target root",
        description="Apply private placeholders from private_secrets.json into an already restored public bundle.",
    )
    secrets_parser.add_argument("--secrets-file", required=True, help="Path to private_secrets.json")
    secrets_parser.add_argument("--target-root", required=True, help="Target root that already contains the restored public bundle")
    secrets_parser.add_argument("--dry-run", action="store_true", help="Show which files would change without modifying them")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "list-agents":
        items = [item.to_dict() for item in list_agents(Path(args.source_root), args.profile)]
        print(json.dumps(items, indent=2))
        return 0

    if args.command == "agent-info":
        root = Path(args.source_root)
        paths = discover_agent_paths(root, agent_name=args.agent_name, agent_path=args.agent_path)
        if not paths:
            parser.error("agent-info requires a valid --agent-name or --agent-path that resolves to an existing agent/workspace")
        print(json.dumps(get_agent_info(root, paths[0], args.profile).to_dict(), indent=2))
        return 0

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
