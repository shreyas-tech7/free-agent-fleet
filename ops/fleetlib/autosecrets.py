"""Make the secrets that the installer owns. The owner never types them.

A secret with `auto: true` in config/secrets.yaml is random. This module makes it once.
A secret with `auto: router_key` is issued by the router. `ops.router.apply` makes those.
"""
import secrets as pysecrets

from . import secrets, tokens


def generate(entry):
    """A random value that matches the format of the entry."""
    # token_urlsafe gives A-Z a-z 0-9 - _ . Every auto format accepts these characters.
    return pysecrets.token_urlsafe(36)


def ensure(only=None, repaired=None):
    """Make every missing auto secret. Returns the names that it made.

    A local token can exist without the hash that its service needs, after a write that stopped half way.
    Such a token gets its hash again, and its name goes into `repaired` when a list is given. See D-053.
    """
    made = []
    for entry in secrets.all_secrets():
        if entry.get("auto") is not True:
            continue
        if only and entry["name"] not in only:
            continue
        if secrets.is_set(entry["name"]):
            if entry["name"] in tokens.DERIVED:
                try:
                    if tokens.repair(entry["name"]) and repaired is not None:
                        repaired.append(entry["name"])
                except (secrets.SecretError, OSError):
                    pass          # the doctor reports a token that stays without its hash
            continue
        try:
            if entry["name"] in tokens.DERIVED:
                tokens.rotate(entry["name"])
            else:
                secrets.set_value(entry["name"], generate(entry))
        except secrets.SecretError:
            continue          # the target folder does not exist yet. A later step makes it.
        made.append(entry["name"])
    return made


def main():
    made = ensure()
    print("made: %s" % (", ".join(made) if made else "nothing (all auto secrets exist)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
