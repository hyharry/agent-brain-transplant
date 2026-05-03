# agent-brain-transplant

Small Python CLI for moving OpenClaw or Hermes-agent state in two parts:

1. `public_bundle/`: files safe to move/share, with secrets masked.
2. `private_secrets.json`: real secret values, applied later.

Chat/session history is treated as ephemeral runtime state and is not backed up.

## Profiles

- `openclaw` defaults to `~/.openclaw`
- `hermes` / `hermes-agent` default to `~/.hermes`

Hermes backups are root-scoped: do not pass `--agent-name` or `--agent-path`. Root `SOUL.md` and `.env` are included when present. If Docker permissions block `memory/`, `skills/`, `workspace/`, or `crons/`, the plan reports warnings.

## Core Commands

```bash
python3 -m agent_brain_transplant list-agents --profile openclaw

python3 -m agent_brain_transplant agent-info \
  --profile openclaw \
  --agent-name abcd

python3 -m agent_brain_transplant plan-backup \
  --profile openclaw \
  --agent-name abcd
```

## Backup

Selected OpenClaw agent:

```bash
python3 -m agent_brain_transplant backup \
  --profile openclaw \
  --agent-name abcd \
  --out-dir ./out/xxxx-backup
```

Hermes root:

```bash
python3 -m agent_brain_transplant backup \
  --profile hermes-agent \
  --out-dir ./out/xxxx-hermes
```

Other backup scopes:

- `backup`: selected agent/workspace plus selected-agent memory/skills/work
- `backup-all`: all agents and shared persistent roots
- `backup-slim`: all agents, memory, skills, and only markdown files from each agent workspace
- `backup-config`: only platform/config/model settings

Example for a transplant bundle that keeps the brain but drops old messaging bindings:

```bash
python3 -m agent_brain_transplant backup-all \
  --profile openclaw \
  --ignore-channel \
  --out-dir ./out/deliver-no-channels
```

Useful options:

```bash
--dry-run
--force
--exclude workspace/agent_code
--exclude '*.sqlite'
--ignore-channel
```

`--exclude workspace/agent_code` also matches agent workspace roots such as `workspace-abcd/agent_code`.

`--ignore-channel` skips channel-related files/config (for example Telegram, WhatsApp, Feishu, Discord, Signal) so you can deliver memory, skills, prompts, and settings into a fresh environment and bind new channels there instead of reusing the old ones.

For `openclaw.json`, `--ignore-channel` also performs a JSON post-check/cleanup pass: it removes embedded channel keys, per-channel config blocks, and channel/agent binding records while keeping the remaining JSON valid. If `openclaw.json` cannot be parsed as valid JSON, the file content is left unchanged and a warning is recorded in the manifest.

## Restore

Restore public files first:

```bash
python3 -m agent_brain_transplant restore-public \
  --bundle-dir ./out/xxxx-backup/public_bundle \
  --target-root /tmp/new-xxxx
```

Then apply private secrets:

```bash
python3 -m agent_brain_transplant apply-secrets \
  --secrets-file ./out/xxxx-backup/private_secrets.json \
  --target-root /tmp/new-xxxx
```

`restore-public` refuses overwrites unless `--force` is set.

## Masking

Text files, including root `.env`, are scanned for sensitive keys such as:

```text
token, secret, password, api_key, client_id, client_secret,
chat_id, account_id, feishu, telegram, allowlist,
allowFrom, appId, group_id
```

Values are replaced with placeholders like:

```text
__ABT_SECRET_password_a1b2c3d4e5__
```

The real values are written to `private_secrets.json`.

## Notes

- `agent-info` is filesystem-inferred: state files, total session count, workspace size, memory size, and skills.
- `manifest.json` is compact; deep workspace paths are summarized after depth 2.
- Binary files are copied as-is.

## Verify

```bash
python3 -m unittest discover -s tests -v
python3 -m agent_brain_transplant --help
```
