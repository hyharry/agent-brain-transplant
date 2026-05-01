from __future__ import annotations

import argparse
import json
from pathlib import Path

from .core import (
    CANONICAL_PROFILES,
    PROFILES,
    PlannerError,
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


def _compact_workspace_display(relative: str) -> str:
    path = Path(relative)
    if path.parts and (path.parts[0] == "workspace" or path.parts[0].startswith("workspace-")) and len(path.parts) > 3:
        return str(Path(*path.parts[:3]) / "...")
    return relative


def _compact_depth_display(relative: str, depth: int = 2) -> str:
    path = Path(relative)
    if len(path.parts) <= depth:
        return relative
    return str(Path(*path.parts[:depth]) / "...")


def _format_plan_summary(manifest) -> str:
    lines = [
        f"profile: {manifest.profile}",
        f"source_root: {manifest.source_root}",
        f"backup_mode: {manifest.backup_mode}",
        f"excludes: {', '.join(manifest.excludes) if manifest.excludes else '-'}",
        f"selected_agent_name: {manifest.selected_agent_name or '-'}",
        f"selected_agent_path: {manifest.selected_agent_path or '-'}",
        f"ignore_channel: {manifest.ignore_channel}",
        f"file_count: {manifest.file_count}",
        "",
        "categories:",
    ]
    for category, count in sorted(manifest.categories.items()):
        lines.append(f"  {category}: {count}")
    if manifest.warnings:
        lines.extend(["", "warnings:"])
        for warning in manifest.warnings:
            lines.append(f"  - {warning}")

    lines.extend(["", f"sample_files (first {PLAN_SAMPLE_LIMIT} per category):"])
    by_category: dict[str, list] = {}
    for file_plan in manifest.files:
        by_category.setdefault(file_plan.category, []).append(file_plan)
    for category in sorted(by_category):
        files = by_category[category]
        lines.append(f"  {category} ({len(files)} files):")
        display_items = []
        seen_display = set()
        for file_plan in files:
            display_relative = _compact_workspace_display(file_plan.relative)
            if display_relative in seen_display:
                continue
            seen_display.add(display_relative)
            display_items.append((display_relative, file_plan))
        for display_relative, file_plan in display_items[:PLAN_SAMPLE_LIMIT]:
            masked = "masked" if file_plan.masked else "raw"
            lines.append(f"    - {display_relative} [{masked}]")
        remaining = len(display_items) - PLAN_SAMPLE_LIMIT
        if remaining > 0:
            lines.append(f"    ... {remaining} more")
    return "\n".join(lines)


def _format_restore_plan(plan) -> str:
    copied = []
    seen = set()
    for relative in plan.copied_files:
        display = _compact_depth_display(relative)
        if display in seen:
            continue
        seen.add(display)
        copied.append(display)
    skipped = []
    seen_skipped = set()
    for relative in plan.skipped_files:
        display = _compact_depth_display(relative)
        if display in seen_skipped:
            continue
        seen_skipped.add(display)
        skipped.append(display)
    return json.dumps(
        {
            "copied_count": len(plan.copied_files),
            "copied_paths": copied,
            "skipped_count": len(plan.skipped_files),
            "skipped_paths": skipped,
        },
        indent=2,
    )


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


def _add_agent_selector_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--agent-name",
        help="Select a named agent/workspace under the source root, e.g. suyu_code_it",
    )
    parser.add_argument(
        "--agent-path",
        help="Select a specific agent/workspace path relative to the source root or absolute",
    )


def _add_exclude_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Exclude a source-root-relative file/folder/glob. Can be repeated. workspace/foo also matches foo inside agent workspace roots.",
    )


def _add_ignore_channel_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--ignore-channel",
        action="store_true",
        help="Skip channel-related files/config (Telegram, WhatsApp, Feishu, etc.) so the delivered agent can be wired to fresh channels later.",
    )


def _add_backup_command(
    sub,
    name: str,
    *,
    mode: str,
    help_text: str,
    description: str,
    include_agent_selector: bool = True,
) -> argparse.ArgumentParser:
    command_parser = sub.add_parser(name, help=help_text, description=description)
    _add_common_source_args(command_parser)
    if include_agent_selector:
        _add_agent_selector_args(command_parser)
    _add_exclude_arg(command_parser)
    _add_ignore_channel_arg(command_parser)
    command_parser.add_argument("--out-dir", required=True, help="Destination directory for public bundle, manifest, and private secrets file")
    command_parser.add_argument("--dry-run", action="store_true", help="Print the backup manifest without writing output files")
    command_parser.add_argument("--force", action="store_true", help="Allow replacing an existing output directory")
    command_parser.set_defaults(backup_mode=mode)
    return command_parser


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
            "  python3 -m agent_brain_transplant backup --profile hermes-agent --out-dir ./out/hermes-root\n"
            "  python3 -m agent_brain_transplant backup-slim --profile openclaw --out-dir ./out/slim\n"
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
    _add_agent_selector_args(info_parser)

    plan_parser = sub.add_parser(
        "plan-backup",
        help="Show what would be included in a backup",
        description="Inspect the selected-source backup scope and print a manifest without writing files.",
    )
    _add_common_source_args(plan_parser)
    _add_agent_selector_args(plan_parser)
    _add_exclude_arg(plan_parser)
    _add_ignore_channel_arg(plan_parser)
    plan_parser.set_defaults(backup_mode="selected")

    _add_backup_command(
        sub,
        "backup",
        mode="selected",
        help_text="Create selected-agent public bundle + private secrets file",
        description=(
            "Create a selected-agent masked public bundle plus a separate private_secrets.json file. "
            "Use --dry-run first if you want to preview the file plan."
        ),
    )
    _add_backup_command(
        sub,
        "backup-all",
        mode="all",
        help_text="Create backup for all detected agents and shared persistent roots",
        description="Create a backup for all detected agents plus shared memory, skills, notes, projects, artifacts, and workspaces.",
        include_agent_selector=False,
    )
    _add_backup_command(
        sub,
        "backup-slim",
        mode="slim",
        help_text="Create all-agents slim backup without large workspace files",
        description="Create an all-agents slim backup that keeps memory, skills, and markdown files from individual agent workspaces.",
        include_agent_selector=False,
    )
    _add_backup_command(
        sub,
        "backup-config",
        mode="config",
        help_text="Create config-only backup",
        description="Create a config-only backup with general platform/config/model settings; no agents, memory, skills, or workspace files.",
        include_agent_selector=False,
    )

    restore_parser = sub.add_parser(
        "restore-public",
        help="Copy the masked public bundle into a target root",
        description="Restore the masked public bundle into a target root. Output paths are summarized at depth 2. Overwrites are refused unless --force is set.",
    )
    restore_parser.add_argument("--bundle-dir", required=True, help="Path to the public_bundle directory")
    restore_parser.add_argument("--target-root", required=True, help="Target root where the public bundle should be restored")
    restore_parser.add_argument("--dry-run", action="store_true", help="Show a compact summary of which files would be copied")
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
        try:
            manifest = build_backup_manifest(
                root,
                args.profile,
                agent_name=args.agent_name,
                agent_path=args.agent_path,
                backup_mode=args.backup_mode,
                excludes=args.exclude,
                ignore_channel=args.ignore_channel,
            )
        except PlannerError as error:
            parser.error(str(error))
        print(_format_plan_summary(manifest))
        return 0

    if args.command in {"backup", "backup-all", "backup-slim", "backup-config"}:
        try:
            result = backup(
                _source_root_for(args.profile, args.source_root),
                args.profile,
                Path(args.out_dir),
                agent_name=getattr(args, "agent_name", None),
                agent_path=getattr(args, "agent_path", None),
                backup_mode=args.backup_mode,
                excludes=args.exclude,
                ignore_channel=args.ignore_channel,
                dry_run=args.dry_run,
                force=args.force,
            )
        except PlannerError as error:
            parser.error(str(error))
        if args.dry_run:
            print(_format_plan_summary(result.manifest))
        else:
            print(
                json.dumps(
                    {
                        "out_dir": str(Path(args.out_dir).expanduser()),
                        "file_count": result.manifest.file_count,
                        "secret_count": len(result.secrets),
                        "warnings": result.manifest.warnings,
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
        print(_format_restore_plan(plan))
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
