# agent-brain-transplant

A small Python CLI for backing up and transplanting an OpenClaw or Hermes-agent setup in two phases:

1. **Public phase**: copy platform settings, agent setup, and work files into a masked public bundle.
2. **Secrets phase**: apply private values from a separate secrets file to make the transplant functional.

The tool is meant to make agent setups portable without mixing ordinary project/config content with tokens, passwords, IDs, OAuth secrets, or similar sensitive values. Chat/session records are treated as ephemeral runtime state and are not backed up.

## What it can do now

- back up OpenClaw or Hermes-agent roots
  - includes memory, skills, and work/project files
  - excludes current and past chat/session records
- list detected agents/workspaces
- show per-agent info
  - state
  - inferred total session count
  - workspace storage size
  - skills
- create a masked public backup bundle
  - default backup scope is the selected agent/workspace plus selected-agent memory/skills/work
  - `backup-config` backs up only general platform/config/model settings
  - `backup-all` backs up all detected agents and shared persistent roots
  - `backup-slim` backs up all agents but keeps only memory, skills, and markdown files from each agent workspace
  - `--exclude` can be repeated to omit source-root-relative files, folders, or globs
- store secrets in a separate private file
- restore in two steps
  - `restore-public`
  - `apply-secrets`
- support dry-run planning
- refuse overwrites by default

## Supported profiles

- `openclaw`
- `hermes`
- `hermes-agent`

`hermes` and `hermes-agent` currently map to the same Hermes-agent profile.

## CLI overview

```bash
python3 -m agent_brain_transplant --help
```

Main commands:

- `list-agents`
- `agent-info`
- `plan-backup`
- `backup`
- `backup-all`
- `backup-slim`
- `backup-config`
- `restore-public`
- `apply-secrets`

## Example usage

### List agents in an OpenClaw root

```bash
python3 -m agent_brain_transplant list-agents \
  --profile openclaw
```

### Show detailed info for one agent

```bash
python3 -m agent_brain_transplant agent-info \
  --profile openclaw \
  --agent-name suyu_code_it
```

### Preview what would be backed up

```bash
python3 -m agent_brain_transplant plan-backup \
  --profile openclaw \
  --agent-name suyu_code_it
```

### Create backup bundle

```bash
python3 -m agent_brain_transplant backup \
  --profile openclaw \
  --agent-name suyu_code_it \
  --out-dir ./out/demo-backup
```

Backup scope commands:

- `backup`: selected agent/workspace plus selected-agent memory/skills/work
- `backup-config`: only general settings and model/config files; no agents, memory, skills, or workspace
- `backup-all`: all detected agents plus shared memory, skills, notes, projects, artifacts, and workspaces
- `backup-slim`: all agents, but only memory, skills, and markdown files from each individual agent workspace
- `--exclude PATTERN`: omit a file, folder, or glob; repeat as needed. `--exclude workspace/agent_code` also matches `agent_code` inside agent workspace roots such as `workspace-yu-code/agent_code`.

This writes:

- `out/demo-backup/public_bundle/` — masked files suitable for broader sync
- `out/demo-backup/manifest.json` — compact backup manifest; deep workspace paths are summarized after depth 2
- `out/demo-backup/private_secrets.json` — sensitive values for separate sync

### Restore public content on a new machine/root

```bash
python3 -m agent_brain_transplant restore-public \
  --bundle-dir ./out/demo-backup/public_bundle \
  --target-root /tmp/new-openclaw
```

### Apply secrets into the transplanted copy

```bash
python3 -m agent_brain_transplant apply-secrets \
  --secrets-file ./out/demo-backup/private_secrets.json \
  --target-root /tmp/new-openclaw
```

### Hermes-agent example

```bash
python3 -m agent_brain_transplant backup \
  --profile hermes-agent \
  --out-dir ./out/hermes-worker
```

Hermes backups are root-scoped. Do not pass `--agent-name` or `--agent-path`; root `SOUL.md` is included when present. If Hermes Docker permissions prevent reading persistent roots such as `memory/`, `skills/`, `workspace/`, or `crons/`, the backup plan reports warnings.

`--source-root` is optional for source commands:

- `openclaw` defaults to `~/.openclaw`
- `hermes` / `hermes-agent` default to `~/.hermes`

## What “agent info” means here

The current implementation reports filesystem-inferred metadata:

- **state**: inferred from `STATE.md` / `state.md` / `TODO.md` when present
- **total session count**: count of current and past session-like JSON/JSONL files under the agent/workspace path
- **workspace storage size**: recursive byte size of the agent/workspace directory
- **memory size**: recursive byte size of the agent/workspace `memory/` directory when present
- **skills**: skill directory names under `skills/`
- **workspace files**: `agent-info` lists files and folders under the selected workspace with human-readable sizes

This is intentionally simple and readable. It is not yet runtime/API-aware.

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
- `feishu`
- `telegram`
- `allowlist`
- `allowFrom`
- `appId`
- `group_id`

Matching values are replaced with placeholders like:

```text
__ABT_SECRET_password_a1b2c3d4e5__
```

Real values are stored in `private_secrets.json` with path and key-hint metadata.

Root `.env` files are included for both OpenClaw and Hermes profiles. They are copied into the public bundle with sensitive values masked, and the real values are stored in `private_secrets.json` for `apply-secrets`.

Binary files are copied as-is.

## Verification

Run tests:

```bash
python3 -m unittest discover -s tests -v
```

Show CLI help:

```bash
python3 -m agent_brain_transplant --help
python3 -m agent_brain_transplant list-agents --help
python3 -m agent_brain_transplant agent-info --help
```

## Limits of this version

This is still a clean vertical slice, not a full migration engine.

Current limits:

- profile discovery is convention-based, not provider-native introspection
- state/session info is filesystem-inferred
- masking is still text-pattern driven rather than schema-aware
- restore does not perform provider-specific re-link flows
- include/exclude rules are profile-level rather than user-configurable

Useful next upgrades would be schema-aware secret masking, diffing, include/exclude filters, and provider-native inspection hooks.
