"""Make config/router/litellm.yaml from config/router/combos.yaml.

LiteLLM is the fallback router. It uses the same virtual model names as OmniRoute.
The file is generated, so the two routers never drift apart.

Usage: python3 -m ops.router.litellm_gen [--check]
"""
import pathlib
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = ROOT / "config" / "router" / "combos.yaml"
OUT = ROOT / "config" / "router" / "litellm.yaml"

# provider id in combos.yaml -> (LiteLLM prefix, secret that holds the key)
PROVIDERS = {
    "groq": ("groq", "GROQ_API_KEY"),
    "gemini": ("gemini", "GEMINI_API_KEY"),
    "openrouter": ("openrouter", "OPENROUTER_API_KEY"),
    "cloudflare-ai": ("cloudflare", "CLOUDFLARE_AI_API_TOKEN"),
    "nvidia": ("nvidia_nim", "NVIDIA_API_KEY"),
    "cohere": ("cohere_chat", "COHERE_API_KEY"),
}

HEADER = """# Generated from config/router/combos.yaml by ops/router/litellm_gen.py. Do not edit by hand.
# LiteLLM is the fallback router. It uses the same virtual model names as OmniRoute.
# Run it only if OmniRoute does not work: litellm --config config/router/litellm.yaml --host 127.0.0.1 --port 20128
# Cloudflare targets need CLOUDFLARE_ACCOUNT_ID in the environment as well as the token.
"""


def build():
    combos = yaml.safe_load(SRC.read_text())["combos"]
    model_list = []
    for name, combo in combos.items():
        if combo.get("dev_only"):
            continue                    # fleet-dev holds trial models. The fallback router never serves it.
        order = 0
        for target in combo["targets"]:
            provider, _, model = target.partition("/")
            if provider == "ollama":
                params = {"model": "ollama/" + model, "api_base": "http://127.0.0.1:11434"}
            elif provider in PROVIDERS:
                prefix, env = PROVIDERS[provider]
                params = {"model": "%s/%s" % (prefix, model), "api_key": "os.environ/%s" % env}
            else:
                continue
            order += 1
            params["order"] = order
            model_list.append({"model_name": name, "litellm_params": params})
    doc = {
        "model_list": model_list,
        "litellm_settings": {"telemetry": False, "drop_params": True, "num_retries": 1, "request_timeout": 120},
        "router_settings": {"routing_strategy": "simple-shuffle", "enable_pre_call_checks": True},
        "general_settings": {"master_key": "os.environ/LITELLM_MASTER_KEY", "disable_spend_logs": True},
    }
    return HEADER + yaml.safe_dump(doc, sort_keys=False, width=120)


def main(argv):
    text = build()
    if "--check" in argv:
        if not OUT.exists() or OUT.read_text() != text:
            print("config/router/litellm.yaml is out of date. Run: python3 -m ops.router.litellm_gen", file=sys.stderr)
            return 1
        return 0
    OUT.write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
