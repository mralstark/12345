"""Apply known upload/comment updates, preserving Certbot TLS.

Unknown template drift is deliberately left to sync_units.sh's existing guard.
"""
import hashlib
from itertools import product
import os
from pathlib import Path
import shutil
import subprocess
import sys

OLD = "location ~ ^/api/(documents|news|profile/me/avatar)$ {"
NEW = "location ~ ^/api/(documents|news|profile/me/avatar|news/articles/[1-9][0-9]*/photos)$ {"
OLD_COMMENT = "включён только для трёх маршрутов, которые действительно принимают файлы."
NEW_COMMENT = "включён только для маршрутов, которые действительно принимают файлы."
OLD_FRAME_COMMENT = "# Mini App внутри Telegram — отдельный WebView; фрейминг разрешён только Telegram."
NEW_FRAME_COMMENT = ("# Встраивание в Telegram/VK ограничивает CSP приложения (api/main.py).\n"
                     "    # Не добавляйте X-Frame-Options: DENY/SAMEORIGIN поверх этого списка.")
OLD_ACCESS_LOG = '                    \'"$http_referer" "$http_user_agent"\';'
NEW_ACCESS_LOG = "                    '$request_time';"


def migrated_config(template, stamp, installed):
    if template.count(NEW) != 1:
        return None
    features = [((OLD, NEW), (OLD_COMMENT, NEW_COMMENT)),
                ((OLD_FRAME_COMMENT, NEW_FRAME_COMMENT),), ((OLD_ACCESS_LOG, NEW_ACCESS_LOG),)]
    # Enumerate exact known predecessors only. Changes to limits/TLS or any
    # other template bytes still invalidate the saved hash and block deployment.
    for mask in product((False, True), repeat=len(features)):
        if not any(mask):
            continue
        pairs = [pair for enabled, feature in zip(mask, features) if enabled for pair in feature]
        if any(template.count(new) != 1 for old, new in pairs):
            continue
        previous = template
        for old, new in pairs:
            previous = previous.replace(new, old)
        if hashlib.sha256(previous.encode()).hexdigest() != stamp.strip():
            continue
        if any(installed.count(old) != 1 for old, new in pairs):
            return None
        candidate = installed
        for old, new in pairs:
            candidate = candidate.replace(old, new)
        return candidate
    return None


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
    print("    nginx: known template update applied; TLS configuration preserved")
    return True


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit("Usage: migrate_nginx_article_uploads.py TEMPLATE STAMP INSTALLED_CONFIG")
    migrate(*(Path(p) for p in sys.argv[1:]))
