#!/usr/bin/env bash
# Private app data must remain private after imports and repeated deployments.
# Deliberately restricted to canonical application data; never follows symlinks.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Run as root" >&2; exit 1; }
umask 077
for directory in /var/lib/bratstvo /opt/bratstvo/backups; do
    [[ ! -L "$directory" ]] || { echo "Refusing a symlinked data root" >&2; exit 1; }
    [[ -d "$directory" ]] || continue
    find -P "$directory" -xdev -type d -exec chown bratstvo:bratstvo {} + -exec chmod 700 {} +
    find -P "$directory" -xdev -type f -exec chown bratstvo:bratstvo {} + -exec chmod 600 {} +
done
