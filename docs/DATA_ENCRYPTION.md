# Personal data encryption and key recovery

The shared ORM encrypts the 49 fields inventoried in
`database/protected_fields.py`. These include identity/provider identifiers,
contacts, names, birth/member dates, application details, free text, private
notes and filenames. Private photographs, avatars, documents and generated
exports are encrypted too. Download capabilities conceal their metadata;
delivery logs use pseudonymous references rather than Telegram IDs.

## Security boundary

We use the maintained `cryptography` implementation of AES-256-SIV (a 512-bit
SIV key, comprising two AES-256 keys), not a custom cipher. Prose uses a random
associated nonce; exact-match identifiers use deterministic, domain-separated
SIV. Equal identifiers remain recognizable as equal, allowing existing joins
and unique indexes. Name ordering/search and private-date aggregates happen
after scoped application decryption. Files authenticate their relative path.

Production credentials are also sealed in `DATA_RUNTIME_CIPHERTEXT`: otherwise
a stolen `.env` bot token could let an attacker impersonate Telegram users and
request legitimate server decryption later. The token, DB URL/password, media
signing key and configured proxy/provider secrets are decrypted only in memory.

This protects a copied DB/dump/private-file directory **without its key**.
It does not hide row counts, relations, ciphertext lengths, roles, public
catalogs, operational dates or every aggregate. Authorized responses contain
plaintext. A live host administrator can read process memory, change the app
or use its legitimate decryption authority. A provider snapshot including RAM
can include the key. An online KMS alone would not remove that authority.
Protection from the live host requires independently trusted client encryption
and a redesign of centralized reports, recovery and bot notifications.
This is not a signed database ledger: an active DB writer can replay valid
ciphertext or alter unencrypted authorization metadata. After a hosting incident,
restore independently authenticated recovery material rather than trusting an
attacker-modifiable database or booting the compromised host image.

Historical plaintext copies cannot be retroactively secured. Do not put an old
plaintext database or cluster snapshot onto a new hosting provider.

## Key custody

`DATA_ENCRYPTION_KEY_FILE` names an absolute private JSON keyring outside source,
uploads and database/storage backups. `CREDENTIALS_DIRECTORY/data-keyring`, when
provided by systemd, takes precedence. The keyring names an active key and maps
key IDs to URL-safe base64-encoded 64-byte keys. Never paste keys into chat,
command arguments, `.env`, Git, image tools or logs.

```text
python -m scripts.data_encryption generate-key --output /private/data-keyring.json
```

The file is created exclusively with owner-only permissions on POSIX. On
Windows, first create an owner-only directory using Windows ACLs. Keep a separate
owner-controlled recovery copy; losing every copy of a required key makes the
corresponding data irrecoverable. Key IDs are not secret. Back up keys separately
from the encrypted data, with independent access control.

On a future Ubuntu host, units load the key from
`/run/bratstvo-keys/data-keyring.json`. Supply it through a trusted SSH connection
after boot from owner-held storage. Verify that `/run` is RAM-backed and disable
unencrypted swap before claiming that the unlock key stays off disk. Set the
fallback `DATA_ENCRYPTION_KEY_FILE` to that RAM path for maintenance commands.
The key file must be readable only by its owner; the runtime account must be
able to read the maintenance path or use systemd's private credential copy.
Do not upload the owner key to `/opt`, `/etc`, a home backup, or a provider secret
store and then claim that the provider lacks it. A host reboot requires unlock;
this availability cost is deliberate. Coredumps and service swap are disabled
in the units, but this is not protection from a hostile live administrator.

## Fresh-target migration

Structural schema upgrades on a legacy source precede this migration. Preserve
an externally encrypted backup. Stop both bot and API for the final copy; the
PostgreSQL copier refuses another client connection to the source database.

Prepare an empty destination DB and a separate empty private storage directory.
Pass credentials through protected environment/configuration, never command
arguments or terminal output:

```text
SOURCE_DATABASE_URL=<old connection, set privately>
SOURCE_STORAGE_DIR=<old private storage>
DATABASE_URL=<new empty connection, set privately>
STORAGE_DIR=<new private storage>
DATA_ENCRYPTION_KEY_FILE=<owner key or RAM credential path>
python -m scripts.data_encryption migrate --confirm-offline
python -m scripts.data_encryption verify
```

The copier supports SQLite and PostgreSQL, preserves IDs, defers only nullable
foreign-key cycle edges, restores their links, preserves identity CHECK
constraints and resets PostgreSQL sequences. It compares all copied values and
counts internally, authenticates every protected value and copied file, and
prints only counts. The source is untouched. Unknown nonempty source tables
require review; do not silently discard them. Old export capabilities expire
and can be regenerated instead of copying the short-lived export cache.

Unlike in-place rewriting, this method never inserts legacy plaintext into the
destination's WAL or obsolete heap pages. A failed copy may leave encrypted
staging files; inspect them or use another empty target before retrying.

Administrative IDs and reviewer names must not remain plaintext in a hosted
`.env`. With the legacy values in a private migration environment, run:

```text
python -m scripts.data_encryption seal-bootstrap --output /private/bootstrap.cipher
```

Set `DATA_BOOTSTRAP_CIPHERTEXT` to the resulting ciphertext and clear
`SUPERUSER_TELEGRAM_IDS`, `AUTO_FEDERAL_TELEGRAM_IDS`,
`PRIMARY_REVIEWER_FULL_NAME` and `OPERATIONS_ALERT_TELEGRAM_ID` in the production
environment. Startup decrypts the equivalent settings in memory. This does not
change existing role permissions.

Seal runtime credentials in the same private preparation environment:

```text
python -m scripts.data_encryption seal-runtime --output /private/runtime.cipher
```

Set `DATA_RUNTIME_CIPHERTEXT` and clear plaintext `BOT_TOKEN`, `DATABASE_URL`,
`MEDIA_SIGNING_KEY`, `BOT_PROXY_URL` and `VK_MINI_APP_SECRET` before production
startup. With an existing runtime ciphertext, the sealing command preserves its
values and applies any explicitly supplied nonempty private overrides; use this
when changing the target DB connection. Clear those overrides after sealing.

Before switching, test login, applications, Academy, private media and report
downloads. Then switch code, DB, storage and matching key together. Startup
authenticates a canary and checks the encryption schema/key identity; it refuses
legacy plaintext storage. Block the application DB role from connecting to any
retained plaintext recovery database. Keep that recovery material locally and
offline, never alongside the new hosted app.

## Rotation, restore and rollback

Create a migration keyring containing the old read keys and a fresh active key:

```text
python -m scripts.data_encryption rotate-key --output /private/rotation-keyring.json
```

Use that keyring while copying the encrypted source into another empty target.
This avoids mixed-key deterministic lookups. Verify the new copy and switch
atomically. Then export only its active key for the running host:
Before dropping old runtime keys, run `seal-bootstrap` again with the rotation
keyring and the existing encrypted settings; it preserves their values while
resealing them under the new active key. Switch that new ciphertext as well.
Also reseal runtime credentials using `seal-runtime` under the rotation keyring.

```text
python -m scripts.data_encryption export-active-key --output /private/runtime-keyring.json
```

Keep historical keys in separate owner-controlled recovery storage for retained
backup generations; do not silently delete them during rotation. A restore must
use the key that matches the restored ciphertext. Rehearse restore, including
canary verification, all protected values and private media.

Rollback is code **plus matching DB/storage/config**, never old plaintext-reading
code against the encrypted target. The deployment rollback checker blocks that
combination. If final acceptance fails, keep writers stopped and restore the
matching pre-cutover source/config; preserve new records before any rollback.

## Verification limits

Crypto/tamper, SQL lookup, dates, migration, upload/download and existing workflow
tests complement CodeQL/Bandit/dependency checks. These do not prove secrecy from
live provider compromise or certify every native Telegram WebView. Do not print
decrypted rows as a verification technique. Measure scoped sorting and file
buffer costs before scaling to a much larger membership.
Private downloads authenticate before sending headers and reserve a bounded
process memory budget until delivery finishes. Client cancellation keeps that
reservation until the background decrypting thread actually stops.
