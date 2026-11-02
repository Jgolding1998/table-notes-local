# Contributor instructions

- Keep this a single-user local tool. Do not add cloud uploads, telemetry, external scripts, remote fonts, accounts or AI service calls without an explicit product decision.
- Never commit real spreadsheets, customer records, credentials, local paths, uploads, browser profiles, databases or generated Python caches. Use synthetic in-memory fixtures.
- Preserve the loopback Host/Origin/CSRF boundary and bounded in-memory uploads. Do not replace the parser with a framework upload handler that silently spools user files to disk.
- Treat all file-derived text as untrusted. Use DOM text APIs; preserve the default summary export's omission of raw rows and filenames.
- Run the documented unit suite. Check the actual UI against the real API when changing their contract; a fixture-only UI test is not integration proof.
- Keep README limits and design notes accurate. Do not claim anonymization, universal security, production hosting, prediction accuracy or personal experience that was not established.
