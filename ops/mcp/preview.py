"""preview_start and preview_stop: serve the static pages of one product on 127.0.0.1 for UI tests.

The product code lives in the working clone and runs in the sandbox. The browser of the Playwright MCP server
runs on the host and cannot reach the sandbox. This tool serves projects/<slug>/app/public from the working clone
on a random local port instead. The Playwright server may open only local addresses.

The server answers GET and HEAD for files only. It never lists a folder. It never follows a link that leaves the folder.
It stops when the tool server stops or when preview_stop runs.
"""
import functools
import http.server
import os
import pathlib
import re
import threading

from ops.fleetlib import paths

SLUG = re.compile(r"^[a-z0-9][a-z0-9\-]{0,40}$")
TOPIC = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,60}$")
MAX_SERVERS = 3
_servers = {}
_lock = threading.Lock()


class PreviewError(Exception):
    pass


def site_dir(project, worktree=None, base=None):
    if not SLUG.match(str(project)):
        raise PreviewError("bad project name")
    root = pathlib.Path(base or paths.work_clone())
    if worktree:
        if not TOPIC.match(str(worktree)):
            raise PreviewError("bad worktree name")
        root = root / ".worktrees" / worktree
    d = (root / "projects" / project / "app" / "public").resolve()
    try:
        d.relative_to(pathlib.Path(base or paths.work_clone()).resolve())
    except ValueError:
        raise PreviewError("the folder is outside the working clone")
    if not d.is_dir():
        raise PreviewError("there is no folder projects/%s/app/public in that worktree" % project)
    return d


class Handler(http.server.SimpleHTTPRequestHandler):
    server_version = "fleet-preview"

    def __init__(self, *a, directory=None, **kw):
        self._base = os.path.realpath(directory)
        super().__init__(*a, directory=directory, **kw)

    def log_message(self, *args):
        return

    def list_directory(self, path):
        self.send_error(404, "no listing")
        return None

    def translate_path(self, path):
        real = os.path.realpath(super().translate_path(path))
        if real != self._base and not real.startswith(self._base + os.sep):
            return os.path.join(self._base, "__outside__")
        return real

    def do_POST(self):
        self.send_error(405)


def start(args, base=None):
    project = str(args.get("project", ""))
    d = site_dir(project, args.get("worktree"), base)
    with _lock:
        if len(_servers) >= MAX_SERVERS and project not in _servers:
            raise PreviewError("at most %d previews run at once. Stop one first." % MAX_SERVERS)
        old = _servers.pop(project, None)
        if old:
            old.shutdown()
            old.server_close()
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(d)))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        _servers[project] = server
    return {"url": "http://127.0.0.1:%d/" % server.server_address[1], "project": project, "folder": str(d.relative_to(pathlib.Path(base or paths.work_clone()).resolve()))}


def stop(args):
    project = str(args.get("project", ""))
    with _lock:
        server = _servers.pop(project, None)
    if server:
        server.shutdown()
        server.server_close()
    return {"stopped": bool(server)}
