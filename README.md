# agent-brain-transplant

Small Python CLI to backup and transplant OpenClaw/Hermes-style agent setups in two phases:

1. **Public phase**: configs/agent files/artifacts with sensitive values masked.
2. **Secrets phase**: apply private values from a separate secrets file.

## Features
- Profile abstraction: `openclaw` and `hermes`.
- Agent selection by `--agent-name` or `--agent-path`.
- Public bundle + manifest output.
- Private secrets file with placeholder mapping.
- Restore in 2 steps: `restore-public` then `apply-secrets`.
- Dry-run plan mode.
- Safe writes by default (no overwrite unless `--force`).

## Quick start
```bash
python3 -m agent_brain_transplant backup \
  --profile openclaw \
  --source-root ~/.openclaw \
  --agent-name suyu_code_it \
  --out-dir ./out/backup1

python3 -m agent_brain_transplant restore-public \
  --bundle-dir ./out/backup1/public_bundle \
  --target-root /tmp/new-openclaw

python3 -m agent_brain_transplant apply-secrets \
  --bundle-dir ./out/backup1/public_bundle \
  --secrets-file ./out/backup1/private_secrets.json \
  --target-root /tmp/new-openclaw
```

## Tests
```bash
python3 -m unittest discover -s tests -v
```
