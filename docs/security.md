# Security Baseline

## Implemented

- Argon2id password hashing
- Password complexity validation
- TOTP 2FA required every login
- Encrypted TOTP secret and VPN private key at rest
- HttpOnly session cookie, SameSite Strict
- Double-submit CSRF token for Portal forms
- Content Security Policy, frame denial, nosniff and referrer policy
- Role checks on API and Portal actions
- Team ownership checks
- File extension and size validation
- Simulator Bearer Token
- Audit Log for privileged and lifecycle actions
- VPN default deny with automatic expiry
- No direct user-provided shell commands

## Required Before Production

1. Put the Portal behind TLS-only reverse proxy and set `COOKIE_SECURE=true`
2. Store all secrets in Vault/KMS/Secrets Manager
3. Add rate limiting for Login, 2FA and Simulator callbacks
4. Add account lockout and security alerting
5. Scan uploaded files with malware/CDR service
6. Replace local Storage with encrypted shared/object storage
7. Add Alembic migration and least-privilege DB account
8. Ship application, audit, WireGuard and simulator logs to SIEM
9. Add backup/restore, retention and PDPA policy
10. Perform SAST, dependency scan, container scan, DAST and penetration test
11. Add CSP nonce if inline scripts are introduced later
12. Add WebAuthn/passkeys as stronger second factor for Admin

## WireGuard Host

- Keep WireGuard keys and interface configuration outside the Web container
- Run the privileged controller on a dedicated host or isolated service
- Restrict sudo to exact scripts and validate arguments
- Apply nftables policy so VPN clients reach only required Simulator services
- Log peer enable/disable and correlate with Booking ID
