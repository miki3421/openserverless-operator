# PostgreSQL security changes

New installations generate independent random PostgreSQL administrator,
replication and platform credentials unless a supported explicit credential is
configured. The existing Secret is authoritative and is preserved on subsequent
reconciliations. Missing Secrets for existing PostgreSQL resources, malformed
credentials, legacy factory passwords and configuration/Secret mismatches fail
closed. This does **not** rotate passwords in an existing PostgreSQL data
volume. Installations using legacy passwords require an explicit coordinated
SQL-role/Secret rotation before applying this change; changing only the Secret
or restarting PostgreSQL does not change an initialized SQL role's password.

PostgreSQL tenants own application objects and can grant themselves object
permissions. ACL revocation therefore cannot provide a reliable read-only quota.
The quota checker now suspends the exact tenant login with `NOLOGIN`, commits
that barrier, terminates its sessions, and verifies they are gone before marking
the quota enforced. This suspends reads as well as writes. Ownership, object
contents and existing grants are preserved. Raising the quota, freeing space
as an administrator, or returning the quota to `auto` restores login. Enforcement
still runs on the configured quota schedule; it is not an instantaneous storage
limit. Existing true quota annotations are rechecked using the new barrier.

The SQL login is derived from `spec.namespace`, independently of the configured
database name. FerretDB uses `<namespace>_ferretdb` and
`<mongodb.database>_ferretdb` respectively. Suspending either login does not
suspend the other login or another tenant.

Focused tests: `python -m unittest discover -s tests -p test_postgres_security.py`.
`tests/postgres_security_live.py` is an explicitly invoked lab test which creates
uniquely named temporary databases, roles and a credential Secret and removes
them in cleanup. It must be run from an operator environment with working cluster
and PostgreSQL administrator access, not as part of automatic unit discovery.
