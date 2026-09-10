# Security Policy

## Reporting a vulnerability

Report security issues privately through
[GitHub Security Advisories](https://github.com/leoalord/spotify-mcp-server/security/advisories/new).
Do not open a public issue for credential leaks, authentication bypasses, or anything that
could expose Spotify or Scalekit tokens.

## Operator notes

- This repository is self-hosted software. Each operator must create their own Spotify developer
  application. The public Hugging Face Space is a reference deployment, not a shared service.
- Hosted mode should set `MCP_ALLOWED_SUBJECTS` to the Scalekit user IDs allowed to connect. If it
  is empty, any identity your Scalekit environment admits can start Spotify OAuth against your
  shared client ID and consume the five-account development-mode quota.
- Never commit `.env`, `TOKEN_ENCRYPTION_KEY`, `DATABASE_URL`, or Spotify refresh tokens. Access
  tokens must stay in memory; refresh tokens belong in the OS keyring or encrypted Neon storage.
