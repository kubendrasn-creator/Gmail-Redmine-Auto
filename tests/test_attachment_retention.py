import os
import time

from backend.storage.attachment_retention import prune_expired_attachments


def test_retention_prunes_old_files_but_keeps_recent_files_and_placeholder(tmp_path):
    root = tmp_path / "attachments"
    root.mkdir()
    old_file = root / "old.bin"
    recent_file = root / "recent.bin"
    placeholder = root / ".gitkeep"
    old_file.write_bytes(b"old")
    recent_file.write_bytes(b"new")
    placeholder.write_text("", encoding="utf-8")
    old_time = time.time() - 3 * 24 * 60 * 60
    os.utime(old_file, (old_time, old_time))

    removed = prune_expired_attachments(root, retention_days=1)

    assert removed == 1
    assert not old_file.exists()
    assert recent_file.exists()
    assert placeholder.exists()
