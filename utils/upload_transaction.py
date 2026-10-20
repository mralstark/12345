"""Remove new upload files if their database transaction is rolled back/closed."""
import logging

from sqlalchemy import event

logger = logging.getLogger(__name__)
_KEY = "new_upload_paths"


def track_upload(session, path):
    sync = session.sync_session
    if not sync.info.get("upload_cleanup_installed"):
        sync.info["upload_cleanup_installed"] = True
        event.listen(sync, "after_commit", _committed)
        event.listen(sync, "after_transaction_end", _ended)
    sync.info.setdefault(_KEY, set()).add(path)


def _committed(session):
    session.info.pop(_KEY, None)


def _ended(session, transaction):
    if transaction.parent is not None:
        return
    for path in session.info.pop(_KEY, set()):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not remove a rolled-back upload")
