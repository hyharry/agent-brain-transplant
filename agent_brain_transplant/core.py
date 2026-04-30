from __future__ import annotations

import hashlib
import fnmatch
import json
import os
import re
import shutil
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterator


TEXT_SUFFIXES = {
    ".json",
    ".jsonc",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".conf",
    ".env",
    ".md",
    ".txt",
    ".py",
    ".sh",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".html",
    ".css",
    ".xml",
    ".csv",
}
SLIM_SUFFIXES = {".md", ".markdown"}
BACKUP_MODES = {"selected", "all", "config", "slim"}
CONFIG_NAME_RE = re.compile(r".*(config|setting|settings|model|models).*", re.IGNORECASE)
SECRET_KEY_PATTERN = (
    r"token|secret|password|passwd|oauth|api[_-]?key|client[_-]?id|client[_-]?secret|"
    r"webhook|session|auth|chat_id|account_id|open_id|user_id|union_id|"
    r"feishu|telegram|tg[_-]?id|allow[_-]?list|allowlist|allowed[_-]?(?:id|ids|user|users)|"
    r"allow[_-]?from|app[_-]?id|group[_-]?id|group[_-]?ids"
)

LINE_SECRET_RE = re.compile(
    rf"(?im)^([ \t\"']*[A-Za-z0-9_.-]*?(?:{SECRET_KEY_PATTERN})[A-Za-z0-9_.-]*[ \t\"']*)([:=])([ \t]*)([^\n#]+)"
)
JSON_STRING_SECRET_RE = re.compile(
    rf'(?i)("[^"\\]*(?:{SECRET_KEY_PATTERN})[^"\\]*"\s*:\s*")([^"\\]*(?:\\.[^"\\]*)*)(")'
)
SESSION_FILE_RE = re.compile(r".*(session|conversation|transcript|chat).*\.(json|jsonl)$", re.IGNORECASE)
SESSION_DIR_NAMES = {
    "session",
    "sessions",
    "chat_session",
    "chat_sessions",
    "current",
    "current_sessions",
    "past",
    "past_sessions",
    "history",
    "histories",
    "archive",
    "archives",
    "conversation",
    "conversations",
    "transcript",
    "transcripts",
    "chat",
    "chats",
}
STATE_FILE_NAMES = {"STATE.md", "state.md", "TODO.md", "todo.md"}
CORE_AGENT_FILES = {"AGENTS.md", "SOUL.md", "USER.md", "HEARTBEAT.md", "MEMORY.md", "IDENTITY.md", "TOOLS.md"}
SKILL_DOC_NAMES = {"SKILL.md"}


@dataclass(frozen=True)
class Profile:
    name: str
    aliases: tuple[str, ...]
    platform_files: list[str]
    agent_roots: list[str]
    shared_roots: list[str]
    work_roots: list[str]


_PROFILE_LIST = [
    Profile(
        name="openclaw",
        aliases=("openclaw",),
        platform_files=["openclaw.json", ".env"],
        agent_roots=["agents", "workspace-*"],
        shared_roots=["memory", "skills", "notes", "projects", "artifacts", "shared-agent-coordination.md"],
        work_roots=["memory", "notes", "projects", "artifacts"],
    ),
    Profile(
        name="hermes-agent",
        aliases=("hermes", "hermes-agent"),
        platform_files=["hermes.json", "hermes-agent.json", "SOUL.md", ".env"],
        agent_roots=["agents", "workspace-*"],
        shared_roots=["memory", "skills", "notes", "projects", "artifacts"],
        work_roots=["memory", "notes", "projects", "artifacts"],
    ),
]

PROFILES = {alias: profile for profile in _PROFILE_LIST for alias in profile.aliases}
CANONICAL_PROFILES = {profile.name: profile for profile in _PROFILE_LIST}


@dataclass
class FilePlan:
    source: str
    relative: str
    category: str
    text_mode: bool
    masked: bool


@dataclass
class BackupManifest:
    profile: str
    source_root: str
    backup_mode: str
    excludes: list[str]
    selected_agent_name: str | None
    selected_agent_path: str | None
    file_count: int
    categories: dict[str, int]
    files: list[FilePlan]
    warnings: list[str]

    def to_dict(self, *, compact_workspace: bool = True) -> dict:
        return {
            "profile": self.profile,
            "source_root": self.source_root,
            "backup_mode": self.backup_mode,
            "excludes": self.excludes,
            "selected_agent_name": self.selected_agent_name,
            "selected_agent_path": self.selected_agent_path,
            "file_count": self.file_count,
            "categories": self.categories,
            "warnings": self.warnings,
            "files": _manifest_file_entries(self.files, compact_workspace=compact_workspace),
        }


@dataclass
class BackupResult:
    manifest: BackupManifest
    secrets: dict[str, dict[str, str]]


@dataclass
class RestorePlan:
    copied_files: list[str]
    skipped_files: list[str]


@dataclass
class ApplySecretsPlan:
    changed_files: list[str]


@dataclass
class AgentInfo:
    name: str
    path: str
    workspace_path: str | None
    platform: str
    state: str
    session_count: int
    workspace_bytes: int
    skill_count: int
    skills: list[str]
    core_files: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


class PlannerError(ValueError):
    pass


def _is_workspace_relative(relative: Path) -> bool:
    return bool(relative.parts and (relative.parts[0] == "workspace" or relative.parts[0].startswith("workspace-")))


def _workspace_manifest_relative(relative: str) -> tuple[str, bool]:
    path = Path(relative)
    if not _is_workspace_relative(path) or len(path.parts) <= 3:
        return relative, False
    return str(Path(*path.parts[:3]) / "..."), True


def _manifest_file_entries(files: list[FilePlan], *, compact_workspace: bool) -> list[dict]:
    if not compact_workspace:
        return [asdict(item) for item in files]

    entries: list[dict] = []
    grouped: dict[str, dict] = {}
    for item in files:
        relative, summarized = _workspace_manifest_relative(item.relative)
        if not summarized:
            entries.append(asdict(item))
            continue
        group = grouped.setdefault(
            relative,
            {
                "relative": relative,
                "category": item.category,
                "text_mode": None,
                "masked": None,
                "summarized": True,
                "file_count": 0,
            },
        )
        group["file_count"] += 1
    return entries + [grouped[key] for key in sorted(grouped)]


def normalize_profile_name(profile_name: str) -> str:
    try:
        return PROFILES[profile_name].name
    except KeyError as error:
        raise PlannerError(f"Unknown profile: {profile_name}") from error


def get_profile(profile_name: str) -> Profile:
    try:
        return PROFILES[profile_name]
    except KeyError as error:
        raise PlannerError(f"Unknown profile: {profile_name}") from error


def _iter_existing_roots(root: Path, patterns: list[str]) -> Iterator[Path]:
    for pattern in patterns:
        if any(ch in pattern for ch in "*?[]"):
            yield from sorted(root.glob(pattern))
        else:
            candidate = root / pattern
            if candidate.exists():
                yield candidate


def _is_readable_root(path: Path) -> bool:
    if path.is_dir():
        return os.access(path, os.R_OK | os.X_OK)
    return os.access(path, os.R_OK)


def _hermes_access_warnings(root: Path) -> list[str]:
    warnings: list[str] = []
    for relative in ("memory", "skills", "workspace", "crons"):
        candidate = root / relative
        if not candidate.exists():
            warnings.append(f"Hermes persistent root not found: {relative}")
        elif not _is_readable_root(candidate):
            warnings.append(f"Hermes persistent root is not accessible: {relative}")
    return warnings


def _safe_file_candidates(source_root: Path, root: Path, warnings: list[str]) -> list[Path]:
    if not _is_readable_root(source_root):
        warnings.append(f"Backup source is not accessible: {_relative_string(source_root, root)}")
        return []
    if source_root.is_file():
        return [source_root]
    try:
        return [item for item in sorted(source_root.rglob("*")) if item.is_file()]
    except OSError as error:
        warnings.append(f"Could not scan backup source {_relative_string(source_root, root)}: {error}")
        return []


def _root_config_files(root: Path, profile: Profile) -> list[Path]:
    files: list[Path] = []
    for file_name in profile.platform_files:
        candidate = root / file_name
        if candidate.exists() and candidate.is_file():
            files.append(candidate)
    for candidate in sorted(root.iterdir()) if root.exists() else []:
        if not candidate.is_file():
            continue
        if candidate.name in profile.platform_files:
            continue
        if candidate.suffix.lower() in TEXT_SUFFIXES and CONFIG_NAME_RE.match(candidate.stem):
            files.append(candidate)
    return files


def _workspace_roots(root: Path) -> list[Path]:
    roots: list[Path] = []
    workspace = root / "workspace"
    if workspace.exists() and workspace.is_dir():
        roots.append(workspace)
    roots.extend(path for path in sorted(root.glob("workspace-*")) if path.is_dir())
    return roots


def _agent_workspace_roots(root: Path, agent_names: list[str]) -> list[Path]:
    roots: list[Path] = []
    for name in agent_names:
        for candidate in _workspace_candidates(root, name):
            if candidate.exists() and candidate.is_dir() and candidate not in roots:
                roots.append(candidate)
    return roots


def _selected_scoped_roots(root: Path, profile: Profile, agent_names: list[str]) -> list[Path]:
    roots: list[Path] = []
    for shared_root in profile.shared_roots + profile.work_roots:
        base = root / shared_root
        if not base.exists() or not base.is_dir():
            continue
        for name in agent_names:
            for candidate in (base / name, base / f"workspace-{name}"):
                if candidate.exists():
                    roots.append(candidate)
    return roots


def discover_agent_paths(root: Path, agent_name: str | None = None, agent_path: str | None = None) -> list[Path]:
    found: list[Path] = []
    if agent_path:
        candidate = Path(agent_path).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        if candidate.exists():
            found.append(candidate)
        return found

    if not agent_name:
        return found

    explicit_candidates = [
        root / "agents" / agent_name,
        root / agent_name,
        root / "workspace" / agent_name,
        root / f"workspace-{agent_name}",
    ]
    if agent_name == "main":
        explicit_candidates.append(root / "workspace")
    for candidate in explicit_candidates:
        if candidate.exists() and candidate not in found:
            found.append(candidate)
    return found


def list_agent_paths(root: Path) -> list[Path]:
    seen: set[Path] = set()
    found: list[Path] = []
    agents_root = root / "agents"
    has_agent_settings = False
    if agents_root.exists():
        for child in sorted(agents_root.iterdir()):
            if child.is_dir() and child not in seen:
                has_agent_settings = True
                seen.add(child)
                found.append(child)
    workspace_root = root / "workspace"
    if not has_agent_settings and workspace_root.exists() and workspace_root.is_dir() and workspace_root not in seen:
        seen.add(workspace_root)
        found.append(workspace_root)
    for child in sorted(root.glob("workspace-*")):
        workspace_name = child.name.removeprefix("workspace-")
        matching_agent = root / "agents" / workspace_name
        if child.is_dir() and child not in seen and not matching_agent.exists():
            seen.add(child)
            found.append(child)
    return found


def _agent_name_from_path(root: Path, path: Path) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return path.name.removeprefix("workspace-")
    if len(relative.parts) >= 2 and relative.parts[0] in {"agents", "workspace"}:
        return relative.parts[1]
    if relative.parts == ("workspace",):
        return "main"
    return path.name.removeprefix("workspace-")


def _workspace_candidates(root: Path, name: str) -> list[Path]:
    candidates = [
        root / f"workspace-{name}",
        root / "workspace" / name,
    ]
    if name == "main":
        candidates.append(root / "workspace")
    return candidates


def _workspace_path_for(root: Path, agent_path: Path, name: str) -> Path | None:
    try:
        relative = agent_path.relative_to(root)
    except ValueError:
        relative = Path()
    if relative.parts and relative.parts[0].startswith("workspace"):
        return agent_path
    for candidate in _workspace_candidates(root, name):
        if candidate.exists() and candidate.is_dir():
            return candidate
    return None


def _agent_names_for_paths(root: Path, paths: list[Path]) -> list[str]:
    names: list[str] = []
    for path in paths:
        name = _agent_name_from_path(root, path)
        if name not in names:
            names.append(name)
    return names


def _all_agent_names(root: Path) -> list[str]:
    return _agent_names_for_paths(root, list_agent_paths(root))


def classify_relative_path(relative: Path, profile: Profile, selected_agent_roots: list[Path], root: Path) -> str:
    if str(relative) in profile.platform_files:
        return "platform"
    for agent_root in selected_agent_roots:
        try:
            relative_to_root = agent_root.relative_to(root)
        except ValueError:
            continue
        if relative == relative_to_root or relative_to_root in relative.parents:
            return "agent"
    if relative.parts and relative.parts[0] in {"projects", "artifacts", "notes", "memory"}:
        return "work"
    return "shared"


def is_text_file(path: Path) -> bool:
    return path.name == ".env" or path.suffix.lower() in TEXT_SUFFIXES


def _placeholder_for(relative_path: str, key_hint: str, value: str, used: set[str]) -> str:
    digest = hashlib.sha1(f"{relative_path}:{key_hint}:{value}".encode("utf-8")).hexdigest()[:10]
    hint = re.sub(r"[^a-z0-9]+", "_", key_hint.lower()).strip("_") or "value"
    base = f"__ABT_SECRET_{hint}_{digest}__"
    candidate = base
    index = 1
    while candidate in used:
        candidate = f"{base}_{index}"
        index += 1
    used.add(candidate)
    return candidate


def mask_text(text: str, relative_path: str, secrets: dict[str, dict[str, str]]) -> tuple[str, bool]:
    used = set(secrets)
    changed = False

    def register(key_hint: str, value: str) -> str:
        placeholder = _placeholder_for(relative_path, key_hint, value.strip(), used)
        secrets[placeholder] = {
            "value": value.strip(),
            "path": relative_path,
            "key_hint": key_hint,
        }
        return placeholder

    def line_repl(match: re.Match[str]) -> str:
        nonlocal changed
        key_text = match.group(1)
        separator = match.group(2)
        spacing = match.group(3)
        value = match.group(4).strip()
        if not value or value.startswith("__ABT_SECRET_"):
            return match.group(0)
        placeholder = register(key_text, value)
        changed = True
        return f"{key_text}{separator}{spacing}{placeholder}"

    masked = LINE_SECRET_RE.sub(line_repl, text)

    def json_repl(match: re.Match[str]) -> str:
        nonlocal changed
        key_text = match.group(1)
        value = match.group(2)
        suffix = match.group(3)
        if not value or value.startswith("__ABT_SECRET_"):
            return match.group(0)
        placeholder = register(key_text, value)
        changed = True
        return f"{key_text}{placeholder}{suffix}"

    masked = JSON_STRING_SECRET_RE.sub(json_repl, masked)
    return masked, changed


def _directory_size(path: Path) -> int:
    total = 0
    for file_path in path.rglob("*"):
        if file_path.is_file():
            try:
                total += file_path.stat().st_size
            except OSError:
                continue
    return total


def _infer_agent_state(agent_path: Path) -> str:
    for name in STATE_FILE_NAMES:
        state_file = agent_path / name
        if state_file.exists():
            text = state_file.read_text(encoding="utf-8", errors="ignore").lower()
            for state in ("in_progress", "blocked", "done", "idle"):
                if state in text:
                    return state
    return "present"


def _session_count(agent_path: Path) -> int:
    count = 0
    for file_path in agent_path.rglob("*"):
        if _is_session_record(file_path, agent_path):
            count += 1
    return count


def _path_parts(path: Path, base_path: Path) -> tuple[str, ...]:
    try:
        relative = path.relative_to(base_path)
    except ValueError:
        relative = Path(path.name)
    return tuple(part.lower() for part in relative.parts)


def _is_session_path(path: Path, base_path: Path) -> bool:
    parts = _path_parts(path, base_path)
    if not parts:
        return False
    parent_names = set(parts[:-1])
    return bool(parts[-1] in SESSION_DIR_NAMES or parent_names & SESSION_DIR_NAMES or SESSION_FILE_RE.match(parts[-1]))


def _relative_string(path: Path, root: Path) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError:
        relative = Path("external") / path.name
    return relative.as_posix()


def _is_excluded(relative: str, excludes: list[str]) -> bool:
    relative = relative.strip("/")
    relative_parts = relative.split("/") if relative else []
    for pattern in excludes:
        normalized = pattern.strip().strip("/")
        if not normalized:
            continue
        if relative == normalized or relative.startswith(f"{normalized}/"):
            return True
        if fnmatch.fnmatch(relative, normalized) or fnmatch.fnmatch(Path(relative).name, normalized):
            return True
        pattern_parts = normalized.split("/")
        if (
            len(pattern_parts) >= 2
            and pattern_parts[0] == "workspace"
            and relative_parts
            and (relative_parts[0] == "workspace" or relative_parts[0].startswith("workspace-"))
        ):
            workspace_relative = "/".join(relative_parts[1:])
            pattern_relative = "/".join(pattern_parts[1:])
            if workspace_relative == pattern_relative or workspace_relative.startswith(f"{pattern_relative}/"):
                return True
            if len(relative_parts) >= 3:
                named_workspace_relative = "/".join(relative_parts[2:])
                if named_workspace_relative == pattern_relative or named_workspace_relative.startswith(f"{pattern_relative}/"):
                    return True
    return False


def _is_slim_file(relative: Path) -> bool:
    if not relative.parts:
        return False
    if relative.parts[0] in {"memory", "skills"}:
        return True
    if "memory" in relative.parts or "skills" in relative.parts:
        return True
    return relative.suffix.lower() in SLIM_SUFFIXES


def _is_session_record(file_path: Path, base_path: Path) -> bool:
    if not file_path.is_file() or file_path.suffix.lower() not in {".json", ".jsonl"}:
        return False
    return _is_session_path(file_path, base_path)


def _skill_names(agent_path: Path) -> list[str]:
    names: list[str] = []
    skills_root = agent_path / "skills"
    if skills_root.exists():
        for skill_dir in sorted(skills_root.iterdir()):
            if skill_dir.is_dir():
                names.append(skill_dir.name)
    return names


def get_agent_info(root: Path, agent_path: Path, profile_name: str) -> AgentInfo:
    name = _agent_name_from_path(root, agent_path)
    workspace_path = _workspace_path_for(root, agent_path, name)
    stat_roots = [agent_path]
    if workspace_path and workspace_path != agent_path:
        stat_roots.append(workspace_path)
    core_files = [name for name in sorted(CORE_AGENT_FILES) if (agent_path / name).exists()]
    return AgentInfo(
        name=name,
        path=str(agent_path),
        workspace_path=str(workspace_path) if workspace_path else None,
        platform=normalize_profile_name(profile_name),
        state=_infer_agent_state(agent_path),
        session_count=sum(_session_count(path) for path in stat_roots),
        workspace_bytes=_directory_size(workspace_path) if workspace_path else 0,
        skill_count=len(_skill_names(agent_path)),
        skills=_skill_names(agent_path),
        core_files=core_files,
    )


def list_agents(root: Path, profile_name: str) -> list[AgentInfo]:
    profile = get_profile(profile_name)
    _ = profile
    return [get_agent_info(root, path, profile_name) for path in list_agent_paths(root)]


def build_backup_manifest(
    root: Path,
    profile_name: str,
    agent_name: str | None = None,
    agent_path: str | None = None,
    backup_mode: str = "selected",
    excludes: list[str] | None = None,
) -> BackupManifest:
    profile = get_profile(profile_name)
    if profile.name == "hermes-agent" and (agent_name or agent_path):
        raise PlannerError("Hermes backups are root-scoped; do not pass --agent-name or --agent-path")
    if backup_mode not in BACKUP_MODES:
        raise PlannerError(f"Unknown backup mode: {backup_mode}")
    excludes = excludes or []
    warnings: list[str] = []
    if profile.name == "hermes-agent":
        warnings.extend(_hermes_access_warnings(root))

    selected_agent_roots = [] if backup_mode in {"all", "config", "slim"} else discover_agent_paths(root, agent_name=agent_name, agent_path=agent_path)
    if backup_mode == "selected" and agent_name and not selected_agent_roots:
        raise PlannerError(f"Could not find agent '{agent_name}' under {root}")
    if backup_mode == "selected" and agent_path and not selected_agent_roots:
        raise PlannerError(f"Could not find agent path '{agent_path}' under {root}")

    seen: set[Path] = set()
    planned_files: list[FilePlan] = []
    category_counts = {"platform": 0, "agent": 0, "shared": 0, "work": 0}

    if backup_mode == "config":
        source_roots = _root_config_files(root, profile)
    elif backup_mode == "all":
        source_roots = list(_iter_existing_roots(root, profile.platform_files + profile.shared_roots + profile.work_roots))
        agents_root = root / "agents"
        if agents_root.exists():
            source_roots.append(agents_root)
        source_roots.extend(_workspace_roots(root))
    elif backup_mode == "slim":
        agent_names = _all_agent_names(root)
        source_roots = list(_root_config_files(root, profile))
        agents_root = root / "agents"
        if agents_root.exists():
            source_roots.append(agents_root)
        for root_name in ("memory", "skills"):
            candidate = root / root_name
            if candidate.exists():
                source_roots.append(candidate)
        source_roots.extend(_agent_workspace_roots(root, agent_names))
    else:
        source_roots = list(_root_config_files(root, profile))
        source_roots.extend(selected_agent_roots)
        source_roots.extend(_selected_scoped_roots(root, profile, _agent_names_for_paths(root, selected_agent_roots)))

    for source_root in source_roots:
        candidates = _safe_file_candidates(source_root, root, warnings)
        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved in seen:
                continue
            try:
                relative = candidate.relative_to(root)
            except ValueError:
                relative = Path("external") / candidate.name
            relative_text = relative.as_posix()
            if _is_session_path(candidate, root) or _is_excluded(relative_text, excludes):
                continue
            category = classify_relative_path(relative, profile, selected_agent_roots, root)
            if backup_mode == "slim" and category != "platform" and not _is_slim_file(relative):
                continue
            seen.add(resolved)
            text_mode = is_text_file(candidate)
            planned_files.append(
                FilePlan(
                    source=str(candidate),
                    relative=str(relative),
                    category=category,
                    text_mode=text_mode,
                    masked=text_mode,
                )
            )
            category_counts[category] = category_counts.get(category, 0) + 1

    return BackupManifest(
        profile=profile.name,
        source_root=str(root),
        backup_mode=backup_mode,
        excludes=excludes,
        selected_agent_name=agent_name,
        selected_agent_path=agent_path,
        file_count=len(planned_files),
        categories=category_counts,
        files=planned_files,
        warnings=warnings,
    )


def _ensure_writable_destination(path: Path, force: bool) -> None:
    if path.exists() and not force:
        raise FileExistsError(f"Refusing to overwrite existing path: {path}")


def backup(
    root: Path,
    profile_name: str,
    out_dir: Path,
    agent_name: str | None = None,
    agent_path: str | None = None,
    backup_mode: str = "selected",
    excludes: list[str] | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> BackupResult:
    root = root.expanduser().resolve()
    out_dir = out_dir.expanduser().resolve()
    manifest = build_backup_manifest(
        root,
        profile_name,
        agent_name=agent_name,
        agent_path=agent_path,
        backup_mode=backup_mode,
        excludes=excludes,
    )

    if dry_run:
        return BackupResult(manifest=manifest, secrets={})

    _ensure_writable_destination(out_dir, force)
    public_bundle = out_dir / "public_bundle"
    private_secrets_file = out_dir / "private_secrets.json"
    manifest_file = out_dir / "manifest.json"
    out_dir.mkdir(parents=True, exist_ok=True)

    secrets: dict[str, dict[str, str]] = {}

    for planned in manifest.files:
        src = Path(planned.source)
        dst = public_bundle / planned.relative
        dst.parent.mkdir(parents=True, exist_ok=True)
        if planned.text_mode:
            try:
                content = src.read_text(encoding="utf-8")
                masked_content, _ = mask_text(content, planned.relative, secrets)
                dst.write_text(masked_content, encoding="utf-8")
            except UnicodeDecodeError:
                shutil.copy2(src, dst)
        else:
            shutil.copy2(src, dst)

    manifest_file.write_text(json.dumps(manifest.to_dict(), indent=2), encoding="utf-8")
    private_secrets_file.write_text(json.dumps(secrets, indent=2), encoding="utf-8")
    return BackupResult(manifest=manifest, secrets=secrets)


def restore_public(bundle_dir: Path, target_root: Path, dry_run: bool = False, force: bool = False) -> RestorePlan:
    bundle_dir = bundle_dir.expanduser().resolve()
    target_root = target_root.expanduser().resolve()
    copied: list[str] = []
    skipped: list[str] = []
    for src in sorted(bundle_dir.rglob("*")):
        if not src.is_file():
            continue
        rel = src.relative_to(bundle_dir)
        dst = target_root / rel
        if dst.exists() and not force:
            skipped.append(str(rel))
            raise FileExistsError(f"Refusing overwrite: {dst}")
        copied.append(str(rel))
        if not dry_run:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    return RestorePlan(copied_files=copied, skipped_files=skipped)


def apply_secrets(target_root: Path, secrets_file: Path, dry_run: bool = False) -> ApplySecretsPlan:
    target_root = target_root.expanduser().resolve()
    secrets = json.loads(secrets_file.expanduser().resolve().read_text(encoding="utf-8"))
    replacements = {placeholder: entry["value"] if isinstance(entry, dict) else str(entry) for placeholder, entry in secrets.items()}
    changed_files: list[str] = []
    for path in sorted(target_root.rglob("*")):
        if not path.is_file() or not is_text_file(path):
            continue
        try:
            original = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        updated = original
        for placeholder, value in replacements.items():
            updated = updated.replace(placeholder, value)
        if updated != original:
            changed_files.append(str(path.relative_to(target_root)))
            if not dry_run:
                path.write_text(updated, encoding="utf-8")
    return ApplySecretsPlan(changed_files=changed_files)


def describe_manifest(manifest: BackupManifest) -> dict:
    return manifest.to_dict()
