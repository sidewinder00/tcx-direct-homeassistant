# Security and sensitive data

Jandy TCX Direct is an experimental personal project. It is not a supported product,
has not received a security audit, and must not be treated as a safety system.

## Do not publish private data

Do not place any of the following in an issue, pull request, discussion, commit, or
other public GitHub content:

- Home Assistant configuration-entry exports or `.storage` files
- Raw Home Assistant or integration debug logs
- Unreviewed Home Assistant diagnostic downloads
- iAquaLink email addresses, passwords, authentication tokens, or session tokens
- Controller IDs, serial numbers, MAC addresses, coordinates, or network details
- Screenshots containing account, device, entity, household, or location information

The integration attempts to redact known sensitive fields from its diagnostic output,
but the reverse-engineered protocol can add previously unknown fields at any time.
Always inspect a diagnostic file manually before sharing it.

## Accidental disclosure

If an iAquaLink password or token is exposed, change the account password and revoke
or refresh active sessions immediately. Removing a secret in a later commit does not
remove it from Git history; exposed credentials must be treated as compromised.

Do not report a sensitive vulnerability through a public issue. This repository does
not currently advertise a confidential reporting channel or guarantee security
response times.
