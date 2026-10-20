"""Apply only the known article-upload location change, preserving Certbot TLS.

Unknown template drift is deliberately left to sync_units.sh's existing guard.
"""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys

OLD = "location ~ ^/api/(documents|news|profile/me/avatar)$ {"
NEW = "location ~ ^/api/(documents|news|profile/me/avatar|news/articles/[1-9][0-9]*/photos)$ {"
OLD_COMMENT = "включён только для трёх маршрутов, которые действительно принимают файлы."
NEW_COMMENT = "включён только для маршрутов, которые действительно принимают файлы."


def migrated_config(template, stamp, installed):
    previous = template.replace(NEW, OLD).replace(NEW_COMMENT, OLD_COMMENT)
    if template.count(NEW) != 1 or hashlib.sha256(previous.encode()).hexdigest() != stamp.strip():
        return None
    if installed.count(OLD) != 1 or NEW in installed:
        return None
    return installed.replace(OLD, NEW)


def replace_file(path, content):
    temporary = path.with_name(path.name + ".article-upload.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        shutil.copymode(path, temporary)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def migrate(template_path, stamp_path, config_path, run=subprocess.run):
    if not all(p.is_file() for p in (template_path, stamp_path, config_path)):
        return False
    template = template_path.read_text(encoding="utf-8")
    original = config_path.read_text(encoding="utf-8")
    candidate = migrated_config(template, stamp_path.read_text(), original)
    if candidate is None:
        return False
    # Keep a recoverable copy, including the host's certificate directives.
    shutil.copy2(config_path, config_path.with_name(config_path.name + ".before-article-uploads"))
    try:
        replace_file(config_path, candidate)
        run(["nginx", "-t"], check=True)
        run(["systemctl", "reload", "nginx"], check=True)
    except BaseException:
        replace_file(config_path, original)
        run(["nginx", "-t"], check=True)
        run(["systemctl", "reload", "nginx"], check=True)
        raise
    replace_file(stamp_path, hashlib.sha256(template.encode()).hexdigest() + "\n")
    print("    nginx: article uploads enabled; TLS configuration preserved")
    return True


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit("Usage: migrate_nginx_article_uploads.py TEMPLATE STAMP INSTALLED_CONFIG")
    migrate(*(Path(p) for p in sys.argv[1:]))
