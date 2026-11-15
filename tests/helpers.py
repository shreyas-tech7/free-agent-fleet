"""Small helpers that unit tests share."""
import os
import pathlib
import shutil


def copy_tree(src, dst):
    """Copy a folder and make the copy writable. The gate container mounts the repository read only.
    A plain copytree would copy the missing write bits, and a test that edits the copy would fail there."""
    src, dst = pathlib.Path(src), pathlib.Path(dst)
    shutil.copytree(src, dst, copy_function=shutil.copyfile)
    for root, dirs, files in os.walk(dst):
        os.chmod(root, 0o755)
        for name in files:
            os.chmod(os.path.join(root, name), 0o644)
    return dst
