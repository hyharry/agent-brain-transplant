# agent-brain-transplant

A small Python CLI for backing up and transplanting an OpenClaw or Hermes-style agent setup in two phases:

1. **Public phase**: copy platform settings, agent setup, and work files into a masked public bundle.
2. **Secrets phase**: apply private values from a separate secrets file to make the transplant functional.

The goal is to make agent setups portable without mixing normal project/config content with tokens, passwords, IDs, OAuth secrets, or similar sensitive values.

## What it backs up

The tool is organized around three buckets:

- **Platform settings**
  - platform config such as `openclaw.json` / `hermes.json`
  - shared skills/config-style files under the selected root
- **Agent-specific setup**
  - agent/workspace folder content such as `AGENTS.md`, `SOUL.md`, heartbeat files, local notes, and related setup files
- **Agentic work**
  - memory, notes, projects, artifacts, and similar created outputs under the selected root

## Current design

Profiles:

- `openclaw`
- `hermes`

Selection options:

- `--agent-name <name>`
- `--agent-path <path>`

Restore is intentionally split into two steps:

- `restore-public`: copy masked files only
- `apply-secrets`: substitute private values from a separate secrets file

By default, public restore refuses to overwrite existing files unless `--force` is used.

## Install / run

No external dependencies are required.

Run directly from the project root:

```bash
python3 -m agent_brain_transplant --help
```

## Example workflow

### 1. Preview what would be backed up

```bash
python3 -m agent_brain_transplant plan-backup \
  --profile openclaw \
  --source-root ~/.openclaw \
  --agent-name suyu_code_it
```

### 2. Create backup bundle

```bash
python3 -m agent_brain_transplant backup \
  --profile openclaw \
  --source-root ~/.openclaw \
  --agent-name suyu_code_it \
  --out-dir ./out/demo-backup
```

This writes:

- `out/demo-backup/public_bundle/` — masked files safe for broader sync
- `out/demo-backup/manifest.json` — backup manifest and file plan
- `out/demo-backup/private_secrets.json` — sensitive values to sync separately

### 3. Restore public content on a new machine/root

```bash
python3 -m agent_brain_transplant restore-public \
  --bundle-dir ./out/demo-backup/public_bundle \
  --target-root /tmp/new-openclaw
```

### 4. Apply secrets into the transplanted copy

```bash
python3 -m agent_brain_transplant apply-secrets \
  --secrets-file ./out/demo-backup/private_secrets.json \
  --target-root /tmp/new-openclaw
```

## Dry-run / planning

- `plan-backup` shows the manifest without writing anything
- `backup --dry-run` also prints the plan only
- `restore-public --dry-run` shows which files would be copied
- `apply-secrets --dry-run` shows which files would change

## Masking behavior

For text-like files, the tool looks for sensitive-looking keys such as:

- `token`
- `secret`
- `password`
- `passwd`
- `oauth`
- `api_key`
- `client_id`
- `client_secret`
- `chat_id`
- `account_id`

Matching values are replaced with placeholders like:

```text
__ABT_SECRET_password_a1b2c3d4e5__
```

The real values are stored in `private_secrets.json` with path and key-hint metadata.

Binary files are copied as-is.

## Project structure

```text
agent_brain_transplant/
  __main__.py
  cli.py
  core.py
tests/
README.md
pyproject.toml
```

## Verification

Run tests:

```bash
python3 -m unittest discover -s tests -v
```

## Limits of this first version

This is a clean, working vertical slice, not a full platform migration engine yet.

Current limits:

- profile discovery is convention-based, not provider-native introspection
- masking is text-pattern driven rather than schema-aware
- binary secrets embedded inside opaque binary formats are not extracted
- restore does not yet perform provider-specific re-link flows; it only substitutes values into restored files
- selective include/exclude rules are still profile-level, not user-configurable

Those are good next steps if you want this expanded.
