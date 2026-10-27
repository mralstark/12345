import hashlib
import re
import subprocess
from pathlib import Path

import pytest

from deploy.migrate_nginx_article_uploads import (
    OLD, NEW, OLD_COMMENT, NEW_COMMENT, OLD_FRAME_COMMENT, NEW_FRAME_COMMENT,
    OLD_ACCESS_LOG, NEW_ACCESS_LOG, migrate, migrated_config,
)

TEMPLATE = Path(__file__).resolve().parents[1] / "deploy" / "nginx-bratstvo.conf"


@pytest.mark.parametrize("path,allowed", [
    ("/api/news/articles/42/photos", True), ("/api/news", True), ("/api/documents", True),
    ("/api/profile/me/avatar", True), ("/api/news/articles", False),
    ("/api/news/articles/42/publish", False), ("/api/news/articles/42/photos/extra", False),
    ("/api/news/articles/no/photos", False), ("/api/register", False),
])
def test_only_upload_routes_get_large_body_limit(path, allowed):
    pattern = re.search(r"location ~ (\S+) \{", TEMPLATE.read_text(encoding="utf-8")).group(1)
    assert bool(re.fullmatch(pattern, path)) is allowed


def setup_files(tmp_path):
    text = TEMPLATE.read_text(encoding="utf-8")
    old = text.replace(NEW, OLD).replace(NEW_COMMENT, OLD_COMMENT)
    template, stamp, installed = (tmp_path / p for p in ("template", "stamp", "vhost"))
    template.write_text(text, encoding="utf-8")
    stamp.write_text(hashlib.sha256(old.encode()).hexdigest())
    # Certbot additions must remain byte-for-byte, not be replaced by template.
    installed.write_text(old + "\n# certbot\nssl_certificate /etc/letsencrypt/live/app/fullchain.pem;\n", encoding="utf-8")
    return template, stamp, installed


def test_known_migration_is_repeatable_preserves_tls_and_checks_before_reload(tmp_path):
    template, stamp, installed = setup_files(tmp_path)
    before = installed.read_text(encoding="utf-8")
    calls = []
    assert migrate(template, stamp, installed, run=lambda cmd, **kw: calls.append(cmd))
    assert installed.read_text(encoding="utf-8") == before.replace(OLD, NEW).replace(OLD_COMMENT, NEW_COMMENT)
    assert calls == [["nginx", "-t"], ["systemctl", "reload", "nginx"]]
    assert not migrate(template, stamp, installed, run=lambda *a, **k: pytest.fail("no repeat reload"))


def test_unknown_template_change_is_not_automatically_approved(tmp_path):
    template, stamp, installed = setup_files(tmp_path)
    changed = template.read_text(encoding="utf-8").replace("512k", "50m")
    assert migrated_config(changed, stamp.read_text(), installed.read_text(encoding="utf-8")) is None


@pytest.mark.parametrize("with_upload_change", [False, True])
def test_exact_frame_comment_predecessors_preserve_installed_tls(with_upload_change):
    text = TEMPLATE.read_text(encoding="utf-8")
    previous = text.replace(NEW_FRAME_COMMENT, OLD_FRAME_COMMENT)
    if with_upload_change:
        previous = previous.replace(NEW, OLD).replace(NEW_COMMENT, OLD_COMMENT)
    installed = previous + "\nssl_certificate /etc/letsencrypt/live/app/fullchain.pem;\n"
    stamp = hashlib.sha256(previous.encode()).hexdigest()
    expected = installed.replace(OLD_FRAME_COMMENT, NEW_FRAME_COMMENT)
    if with_upload_change:
        expected = expected.replace(OLD, NEW).replace(OLD_COMMENT, NEW_COMMENT)
    assert migrated_config(text, stamp, installed) == expected
    assert migrated_config(text.replace("512k", "50m"), stamp, installed) is None


@pytest.mark.parametrize("upload,frame", [(False, False), (False, True), (True, False), (True, True)])
def test_private_access_log_migration_keeps_tls_and_unknown_drift_guard(upload, frame):
    text = TEMPLATE.read_text(encoding="utf-8")
    previous = text.replace(NEW_ACCESS_LOG, OLD_ACCESS_LOG)
    if upload:
        previous = previous.replace(NEW, OLD).replace(NEW_COMMENT, OLD_COMMENT)
    if frame:
        previous = previous.replace(NEW_FRAME_COMMENT, OLD_FRAME_COMMENT)
    installed = previous + "\nssl_certificate /private/cert.pem;\n"
    stamp = hashlib.sha256(previous.encode()).hexdigest()
    assert migrated_config(text, stamp, installed) == text + "\nssl_certificate /private/cert.pem;\n"
    assert migrated_config(text.replace("512k", "50m"), stamp, installed) is None


def test_nginx_validation_failure_restores_vhost_and_stamp(tmp_path):
    template, stamp, installed = setup_files(tmp_path)
    original, old_stamp = installed.read_bytes(), stamp.read_bytes()
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        if len(calls) == 1:
            raise subprocess.CalledProcessError(1, command)
    with pytest.raises(subprocess.CalledProcessError):
        migrate(template, stamp, installed, run=run)
    assert installed.read_bytes() == original and stamp.read_bytes() == old_stamp
    assert calls[1:] == [["nginx", "-t"], ["systemctl", "reload", "nginx"]]
