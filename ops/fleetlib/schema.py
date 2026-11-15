"""JSON Schema checks that return messages and never raise."""
import jsonschema


def validate(instance, schema):
    validator = jsonschema.Draft202012Validator(schema)
    errors = []
    for err in sorted(validator.iter_errors(instance), key=lambda e: list(e.path)):
        where = "/".join(str(p) for p in err.path) or "(root)"
        errors.append("%s: %s" % (where, err.message))
    return errors
