# Sites online trial
- This directory is the JavaScript adaptation of the Django accounting rules. GitHub remains the source of truth; edit remotely and run checks in GitHub Actions.
- Keep all money as fixed-point BigInt during calculation and decimal strings in persisted JSON. Never use binary floating point for money.
- D1 uses one versioned order document per order. Every write must atomically compare the version and current account version, and append an idempotency receipt whose CHECK constraint aborts failed comparisons.
- Preserve historical financial entries and shipment snapshots. Corrections and freight revisions append history. Only completely unallocated mistaken transactions can be reversed.
- Store private vouchers and immutable archives in R2. Authorize every API request and every download. Anonymous visitors see only the login page.
- Use the system's username/password login as explicitly requested by the user for colleagues without ChatGPT accounts. Keep Sites dispatch integration intact; never implement its reserved authentication routes.
- All credentials and signing secrets are private runtime environment variables. Never commit them, account hashes, live databases, or real customer data.
- Test the port with pure accounting cases, D1 compare-and-swap races, permissions, repeat submissions, template import, and browser workflows. D1 concurrency checks are distinct from the Django PostgreSQL suite.
