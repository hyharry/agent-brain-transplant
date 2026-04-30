# agent-brain-transplant

A small Python CLI for backing up and transplanting an OpenClaw or Hermes-agent setup in two phases:

1. **Public phase**: copy platform settings, agent setup, and work files into a masked public bundle.
2. **Secrets phase**: apply private values from a separate secrets file to make the transplant functional.

The tool is meant to make agent setups portable without mixing ordinary project/config content with tokens, passwords, IDs, OAuth secrets, or similar sensitive values.

## What it can do now

- back up OpenClaw or Hermes-agent roots
- list detected agents/workspaces
- show per-agent info
  - state
  - inferred session count
  - workspace storage size
  - skills
  - core agent files present
- create a masked public backup bundle
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
- `restore-public`
- `apply-secrets`

## Example usage

### List agents in an OpenClaw root

```bash
python3 -m agent_brain_transplant list-agents \
  --profile openclaw \
  --source-root ~/.openclaw
```

### Show detailed info for one agent

```bash
python3 -m agent_brain_transplant agent-info \
  --profile openclaw \
  --source-root ~/.openclaw \
  --agent-name suyu_code_it
```

### Preview what would be backed up

```bash
python3 -m agent_brain_transplant plan-backup \
  --profile openclaw \
  --source-root ~/.openclaw \
  --agent-name suyu_code_it
```

### Create backup bundle

```bash
python3 -m agent_brain_transplant backup \
  --profile openclaw \
  --source-root ~/.openclaw \
  --agent-name suyu_code_it \
  --out-dir ./out/demo-backup
```

This writes:

- `out/demo-backup/public_bundle/` — masked files suitable for broader sync
- `out/demo-backup/manifest.json` — file plan and manifest
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
  --source-root ~/.hermes \
  --agent-name worker \
  --out-dir ./out/hermes-worker
```

## What “agent info” means here

The current implementation reports filesystem-inferred metadata:

- **state**: inferred from `STATE.md` / `state.md` / `TODO.md` when present
- **session count**: count of session-like JSON files under the agent/workspace path
- **workspace storage size**: recursive byte size of the agent/workspace directory
- **skills**: skill directory names under `skills/`

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

Matching values are replaced with placeholders like:

```text
__ABT_SECRET_password_a1b2c3d4e5__
```

Real values are stored in `private_secrets.json` with path and key-hint metadata.

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
