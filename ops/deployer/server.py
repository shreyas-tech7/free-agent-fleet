"""The Deployer service on 127.0.0.1:8097.

  POST /deploy {project, commit}     token: DEPLOYER_API_TOKEN
  GET  /health

The Cloudflare token lives in this service's own secret file. The build agent only holds a local token.
"""
import json
import pathlib

from ops.control import approvals
from ops.deployer.cloudflare import Cloudflare
from ops.deployer.core import Deployer, GitRepo
from ops.fleetlib import config, httpd, jsonio, log, paths, pause, secrets, tokens

LOG = log.get("deployer")


def merger_tested(commit):
    results = jsonio.read_json(paths.ops_state_dir() / "merger" / "results.json", {}) or {}
    return (results.get(commit) or {}).get("status") == "pass"


def build(cfg):
    cf = Cloudflare(secrets.value_of("CLOUDFLARE_API_TOKEN"), secrets.value_of("CLOUDFLARE_DEPLOYER_ACCOUNT_ID"))
    return Deployer(cfg, cf, GitRepo(paths.live_repo()), merger_tested, paths.ops_state_dir() / "deployer", paused=pause.paused,
                    approved=approvals.is_approved)


def main():
    cfg = config.load()
    deployer = build(cfg)

    def deploy(req):
        if not httpd.verify_token(req.bearer(), tokens.hashes("DEPLOYER_API_TOKEN")):
            raise httpd.HttpError(401, "a valid bearer token is required")
        return 200, deployer.deploy(req.json())

    routes = {("POST", "/deploy"): deploy, ("GET", "/health"): lambda r: (200, {"status": "OK", "mode": deployer.mode})}
    server = httpd.JsonServer("127.0.0.1", config.port(cfg, "deployer"), routes, max_body=20_000, log=LOG)
    LOG.info("started", port=server.port, mode=deployer.mode)
    server.serve_forever()


if __name__ == "__main__":
    main()
