# Request
Build a minimal, real Python tool to backup/transplant OpenClaw or Hermes agent brains with public/private split and two-phase restore.

# Plan
1. Create modular CLI package.
2. Implement profile discovery + file selection.
3. Implement masking with placeholders + private secrets file.
4. Implement restore public phase and secrets apply phase.
5. Add dry-run/planning and safe overwrite guards.
6. Add unit tests + docs.
7. Verify, commit, set remote, push.

# Outcome
Completed vertical slice implementation with modular CLI and core backup/restore flow.

- Commit: `a760802`
- Remote: `origin git@github.com:hyharry/agent-brain-transplant.git`
- Push: `main` pushed successfully
