from __future__ import annotations
import json, re, shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

SECRET_PATTERNS = [
    re.compile(r"(?i)(token|secret|password|passwd|oauth|api[_-]?key|client[_-]?id|client[_-]?secret|id)\s*[:=]\s*['\"]?([^'\"\n]+)"),
]

@dataclass
class Profile:
    name: str
    include_paths: list[str]

PROFILES = {
    "openclaw": Profile("openclaw", ["openclaw.json", "agents", "memory", "notes", "projects", "artifacts", "skills"]),
    "hermes": Profile("hermes", ["hermes.json", "agents", "memory", "notes", "projects", "artifacts", "skills"]),
}

def selected_agent_paths(root: Path, agent_name: str | None, agent_path: str | None) -> list[Path]:
    if agent_path:
        return [Path(agent_path)]
    if agent_name:
        candidates = [root / "agents" / agent_name, root / agent_name]
        return [p for p in candidates if p.exists()]
    return []

def iter_source_files(root: Path, profile: Profile, agent_paths: list[Path]) -> Iterable[Path]:
    for rel in profile.include_paths:
        p = root / rel
        if p.exists():
            if p.is_file():
                yield p
            else:
                for f in p.rglob("*"):
                    if f.is_file():
                        yield f
    for ap in agent_paths:
        if ap.exists():
            for f in ap.rglob("*"):
                if f.is_file():
                    yield f

def mask_text(text: str, secret_map: dict[str, str]) -> str:
    count = len(secret_map)
    out = text
    for pat in SECRET_PATTERNS:
        def repl(m):
            nonlocal count
            key = m.group(1)
            val = m.group(2)
            placeholder = f"__ABT_SECRET_{count}__"
            secret_map[placeholder] = val
            count += 1
            return f"{key}={placeholder}"
        out = pat.sub(repl, out)
    return out

def backup(root: Path, profile_name: str, out_dir: Path, agent_name: str|None, agent_path: str|None, dry_run: bool=False, force: bool=False):
    profile = PROFILES[profile_name]
    bundle = out_dir / "public_bundle"
    secrets_file = out_dir / "private_secrets.json"
    manifest_file = out_dir / "manifest.json"
    if out_dir.exists() and not force:
        raise FileExistsError(f"Output exists: {out_dir}")
    files=[]
    secrets={}
    for src in iter_source_files(root, profile, selected_agent_paths(root, agent_name, agent_path)):
        rel = src.relative_to(root) if src.is_relative_to(root) else Path("external") / src.name
        files.append(str(rel))
        if dry_run:
            continue
        dst = bundle / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            txt = src.read_text(encoding="utf-8")
            dst.write_text(mask_text(txt, secrets), encoding="utf-8")
        except UnicodeDecodeError:
            shutil.copy2(src, dst)
    if not dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        manifest = {"profile": profile_name, "root": str(root), "files": files}
        manifest_file.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        secrets_file.write_text(json.dumps(secrets, indent=2), encoding="utf-8")
    return files

def restore_public(bundle_dir: Path, target_root: Path, dry_run: bool=False, force: bool=False):
    for src in bundle_dir.rglob("*"):
        if not src.is_file():
            continue
        rel = src.relative_to(bundle_dir)
        dst = target_root / rel
        if dst.exists() and not force:
            raise FileExistsError(f"Refusing overwrite: {dst}")
        if not dry_run:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

def apply_secrets(target_root: Path, secrets_file: Path, dry_run: bool=False):
    secrets = json.loads(secrets_file.read_text(encoding="utf-8"))
    for f in target_root.rglob("*"):
        if not f.is_file():
            continue
        try:
            txt = f.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        new = txt
        for k,v in secrets.items():
            new = new.replace(k, v)
        if new != txt and not dry_run:
            f.write_text(new, encoding="utf-8")
