from __future__ import annotations

import hashlib
import json
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

LINE_SECRET_RE = re.compile(
    r"(?im)^([ \t\"']*[A-Za-z0-9_.-]*?(?:token|secret|password|passwd|oauth|api[_-]?key|client[_-]?id|client[_-]?secret|webhook|session|auth|chat_id|account_id|open_id|user_id|union_id)[A-Za-z0-9_.-]*[ \t\"']*)([:=])([ \t]*)([^\n#]+)"
)
JSON_STRING_SECRET_RE = re.compile(
    r'(?i)("[^"\\]*(?:token|secret|password|passwd|oauth|api[_-]?key|client[_-]?id|client[_-]?secret|webhook|session|auth|chat_id|account_id|open_id|user_id|union_id)[^"\\]*"\s*:\s*")([^"\\]*(?:\\.[^"\\]*)*)(")'
)
SESSION_FILE_RE = re.compile(r".*session.*\.json$", re.IGNORECASE)
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
        platform_files=["openclaw.json"],
        agent_roots=["agents", "workspace-*"],
        shared_roots=["memory", "skills", "notes", "projects", "artifacts", "shared-agent-coordination.md"],
        work_roots=["memory", "notes", "projects", "artifacts"],
    ),
    Profile(
        name="hermes-agent",
        aliases=("hermes", "hermes-agent"),
        platform_files=["hermes.json", "hermes-agent.json"],
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
    selected_agent_name: str | None
    selected_agent_path: str | None
    file_count: int
    categories: dict[str, int]
    files: list[FilePlan]

    def to_dict(self) -> dict:
        return {
            "profile": self.profile,
            "source_root": self.source_root,
            "selected_agent_name": self.selected_agent_name,
            "selected_agent_path": self.selected_agent_path,
            "file_count": self.file_count,
            "categories": self.categories,
            "files": [asdict(item) for item in self.files],
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
        root / f"workspace-{agent_name}",
    ]
    for candidate in explicit_candidates:
        if candidate.exists() and candidate not in found:
            found.append(candidate)
    return found


def list_agent_paths(root: Path) -> list[Path]:
    seen: set[Path] = set()
    found: list[Path] = []
    agents_root = root / "agents"
    if agents_root.exists():
        for child in sorted(agents_root.iterdir()):
            if child.is_dir() and child not in seen:
                seen.add(child)
                found.append(child)
    for child in sorted(root.glob("workspace-*")):
        if child.is_dir() and child not in seen:
            seen.add(child)
            found.append(child)
    return found


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
    return path.suffix.lower() in TEXT_SUFFIXES


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
        if file_path.is_file() and SESSION_FILE_RE.match(file_path.name):
            count += 1
    return count


def _skill_names(agent_path: Path) -> list[str]:
    names: list[str] = []
    skills_root = agent_path / "skills"
    if skills_root.exists():
        for skill_dir in sorted(skills_root.iterdir()):
            if skill_dir.is_dir():
                names.append(skill_dir.name)
    return names


def get_agent_info(root: Path, agent_path: Path, profile_name: str) -> AgentInfo:
    core_files = [name for name in sorted(CORE_AGENT_FILES) if (agent_path / name).exists()]
    return AgentInfo(
        name=agent_path.name.removeprefix("workspace-"),
        path=str(agent_path),
        platform=normalize_profile_name(profile_name),
        state=_infer_agent_state(agent_path),
        session_count=_session_count(agent_path),
        workspace_bytes=_directory_size(agent_path),
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
) -> BackupManifest:
    profile = get_profile(profile_name)
    selected_agent_roots = discover_agent_paths(root, agent_name=agent_name, agent_path=agent_path)
    if agent_name and not selected_agent_roots:
        raise PlannerError(f"Could not find agent '{agent_name}' under {root}")
    if agent_path and not selected_agent_roots:
        raise PlannerError(f"Could not find agent path '{agent_path}' under {root}")

    seen: set[Path] = set()
    planned_files: list[FilePlan] = []
    category_counts = {"platform": 0, "agent": 0, "shared": 0, "work": 0}

    source_roots = list(_iter_existing_roots(root, profile.platform_files + profile.shared_roots + profile.work_roots))
    source_roots.extend(selected_agent_roots)

    for source_root in source_roots:
        if source_root.is_file():
            candidates = [source_root]
        else:
            candidates = [item for item in sorted(source_root.rglob("*")) if item.is_file()]
        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            try:
                relative = candidate.relative_to(root)
            except ValueError:
                relative = Path("external") / candidate.name
            category = classify_relative_path(relative, profile, selected_agent_roots, root)
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
        selected_agent_name=agent_name,
        selected_agent_path=agent_path,
        file_count=len(planned_files),
        categories=category_counts,
        files=planned_files,
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
    dry_run: bool = False,
    force: bool = False,
) -> BackupResult:
    root = root.expanduser().resolve()
    out_dir = out_dir.expanduser().resolve()
    manifest = build_backup_manifest(root, profile_name, agent_name=agent_name, agent_path=agent_path)

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
