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
    discover_agent_paths,
    get_agent_info,
    list_agents,
    restore_public,
)


DEFAULT_SOURCE_ROOTS = {
    "openclaw": "~/.openclaw",
    "hermes": "~/.hermes",
    "hermes-agent": "~/.hermes",
}
PLAN_SAMPLE_LIMIT = 8


def _profiles_help() -> str:
    return ", ".join(f"{name} ({'/'.join(profile.aliases)})" for name, profile in CANONICAL_PROFILES.items())


def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def _source_root_for(profile_name: str, source_root: str | None) -> Path:
    if source_root:
        return Path(source_root).expanduser()
    return Path(DEFAULT_SOURCE_ROOTS[profile_name]).expanduser()


def _directory_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    for file_path in path.rglob("*"):
        if file_path.is_file():
            try:
                total += file_path.stat().st_size
            except OSError:
                continue
    return total


def _format_table(rows: list[dict[str, str]], columns: list[tuple[str, str]]) -> str:
    if not rows:
        return ""
    widths = {
        key: max(len(heading), *(len(row[key]) for row in rows))
        for key, heading in columns
    }
    lines = [
        "  ".join(heading.ljust(widths[key]) for key, heading in columns),
        "  ".join("-" * widths[key] for key, heading in columns),
    ]
    for row in rows:
        lines.append("  ".join(row[key].ljust(widths[key]) for key, heading in columns))
    return "\n".join(lines)


def _format_agent_list(items) -> str:
    rows = []
    for item in items:
        workspace_path_text = item.workspace_path or "-"
        workspace_path = Path(item.workspace_path).expanduser() if item.workspace_path else None
        memory_size = _directory_size(workspace_path / "memory") if workspace_path else 0
        rows.append(
            {
                "name": item.name,
                "total_sessions": str(item.session_count),
                "workspace_path": workspace_path_text,
                "workspace_size": _human_size(item.workspace_bytes),
                "skills_count": str(item.skill_count),
                "skills": ", ".join(item.skills) if item.skills else "-",
                "memory_size": _human_size(memory_size),
            }
        )
    return _format_table(
        rows,
        [
            ("name", "name"),
            ("total_sessions", "total_sessions"),
            ("workspace_path", "workspace_path"),
            ("workspace_size", "workspace_size"),
            ("skills_count", "skills_count"),
            ("skills", "skills"),
            ("memory_size", "memory_size"),
        ],
    )


def _format_workspace_entries(workspace_path: Path) -> str:
    if not workspace_path.exists():
        return "  - missing"
    rows = []
    candidates = [workspace_path]
    candidates.extend(
        path
        for path in sorted(workspace_path.rglob("*"))
        if ".git" not in path.relative_to(workspace_path).parts
        and len(path.relative_to(workspace_path).parts) <= 2
    )
    for path in candidates:
        try:
            relative = path.relative_to(workspace_path)
        except ValueError:
            relative = path
        kind = "dir" if path.is_dir() else "file"
        rows.append(
            {
                "path": "." if relative == Path(".") else str(relative) + ("/" if path.is_dir() else ""),
                "type": kind,
                "size": _human_size(_directory_size(path)),
            }
        )
    if not rows:
        return "  - empty"
    return _format_table(rows, [("path", "path"), ("type", "type"), ("size", "size")])


def _format_agent_info(info) -> str:
    workspace_path = Path(info.workspace_path).expanduser() if info.workspace_path else None
    lines = [
        f"name: {info.name}",
        f"platform: {info.platform}",
        f"agent_settings_path: {info.path}",
        f"workspace_path: {info.workspace_path or '-'}",
        f"state: {info.state}",
        f"session_count: {info.session_count}",
        f"workspace_size: {_human_size(info.workspace_bytes)}",
        f"memory_size: {_human_size(_directory_size(workspace_path / 'memory') if workspace_path else 0)}",
        f"skills_count: {info.skill_count}",
        f"skills: {', '.join(info.skills) if info.skills else '-'}",
        "",
        "workspace:",
        _format_workspace_entries(workspace_path) if workspace_path else "  - missing",
    ]
    return "\n".join(lines)


def _format_plan_summary(manifest) -> str:
    lines = [
        f"profile: {manifest.profile}",
        f"source_root: {manifest.source_root}",
        f"selected_agent_name: {manifest.selected_agent_name or '-'}",
        f"selected_agent_path: {manifest.selected_agent_path or '-'}",
        f"file_count: {manifest.file_count}",
        "",
        "categories:",
    ]
    for category, count in sorted(manifest.categories.items()):
        lines.append(f"  {category}: {count}")

    lines.extend(["", f"sample_files (first {PLAN_SAMPLE_LIMIT} per category):"])
    by_category: dict[str, list] = {}
    for file_plan in manifest.files:
        by_category.setdefault(file_plan.category, []).append(file_plan)
    for category in sorted(by_category):
        files = by_category[category]
        lines.append(f"  {category} ({len(files)} files):")
        for file_plan in files[:PLAN_SAMPLE_LIMIT]:
            masked = "masked" if file_plan.masked else "raw"
            lines.append(f"    - {file_plan.relative} [{masked}]")
        remaining = len(files) - PLAN_SAMPLE_LIMIT
        if remaining > 0:
            lines.append(f"    ... {remaining} more")
    return "\n".join(lines)


def _add_common_source_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--profile",
        choices=sorted(PROFILES),
        required=True,
        help=f"Source platform profile. Supported: {_profiles_help()}",
    )
    parser.add_argument(
        "--source-root",
        help="Root directory of the source setup. Defaults to ~/.openclaw for openclaw, ~/.hermes for hermes/hermes-agent",
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
            "  python3 -m agent_brain_transplant list-agents --profile openclaw\n"
            "  python3 -m agent_brain_transplant agent-info --profile openclaw --agent-name suyu_code_it\n"
            "  python3 -m agent_brain_transplant backup --profile hermes-agent --agent-name worker --out-dir ./out/worker\n"
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
    list_parser.add_argument("--source-root", help="Root directory to scan. Defaults to ~/.openclaw for openclaw, ~/.hermes for hermes/hermes-agent")

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
        source_root = _source_root_for(args.profile, args.source_root)
        items = list_agents(source_root, args.profile)
        print(_format_agent_list(items) if items else f"No agents found under {source_root}")
        return 0

    if args.command == "agent-info":
        root = _source_root_for(args.profile, args.source_root)
        paths = discover_agent_paths(root, agent_name=args.agent_name, agent_path=args.agent_path)
        if not paths:
            parser.error("agent-info requires a valid --agent-name or --agent-path that resolves to an existing agent/workspace")
        print(_format_agent_info(get_agent_info(root, paths[0], args.profile)))
        return 0

    if args.command == "plan-backup":
        root = _source_root_for(args.profile, args.source_root)
        manifest = build_backup_manifest(
            root,
            args.profile,
            agent_name=args.agent_name,
            agent_path=args.agent_path,
        )
        print(_format_plan_summary(manifest))
        return 0

    if args.command == "backup":
        result = backup(
            _source_root_for(args.profile, args.source_root),
            args.profile,
            Path(args.out_dir),
            agent_name=args.agent_name,
            agent_path=args.agent_path,
            dry_run=args.dry_run,
            force=args.force,
        )
        if args.dry_run:
            print(_format_plan_summary(result.manifest))
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
