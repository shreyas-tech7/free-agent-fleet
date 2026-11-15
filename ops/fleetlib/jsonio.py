"""Small file helpers: atomic writes and JSON load."""
import json
import os
import pathlib
import tempfile


def atomic_write(path, data, mode=0o644, owner=None, group=None):
    """Write data (str or bytes) to path in one step. Set the mode before the file appears."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        data = data.encode()
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if owner is not None or group is not None:
            shutil_chown(tmp, owner, group)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    return path


def shutil_chown(path, owner=None, group=None):
    import shutil
    shutil.chown(path, user=owner, group=group)


def read_json(path, default=None):
    try:
        return json.loads(pathlib.Path(path).read_text())
    except (OSError, ValueError):
        return default


def write_json(path, obj, mode=0o644, owner=None, group=None):
    return atomic_write(path, json.dumps(obj, indent=2, sort_keys=True) + "\n", mode, owner, group)
