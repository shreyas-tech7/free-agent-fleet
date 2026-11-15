# Free model providers

The fleet calls models through a local router. The router has four virtual models: `fleet-smart`, `fleet-worker`, `fleet-coder`, and `fleet-local`. Each one is a list of free targets. If one target fails or hits its limit, the router tries the next one.

You need one provider key at minimum. More keys give more free capacity. Each key goes in with `fleetctl secret set NAME`. Run `fleetctl secret check` to test it for free. Then run `fleetctl router apply`.

The fleet refuses any paid model in code. See `ops/router/freeonly.py`.

| Provider | Secret names | Get a key | Notes |
|---|---|---|---|
| Google Gemini | `GEMINI_API_KEY` | https://aistudio.google.com/apikey | Free tier. Limits change. Read the terms about the use of prompts. |
| Groq | `GROQ_API_KEY` | https://console.groq.com/keys | Free plan with daily limits. Its limit of 8,000 tokens a minute is too small for an agent call, so no agent combo uses it. |
| OpenRouter | `OPENROUTER_API_KEY` | https://openrouter.ai/keys | The fleet uses only models whose names end with `:free`. Free models may log prompts. |
| Cloudflare Workers AI | `CLOUDFLARE_AI_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` | https://dash.cloudflare.com/profile/api-tokens | Free daily allocation. |
| NVIDIA API catalog | `NVIDIA_API_KEY` | https://build.nvidia.com/settings/api-keys | Trial terms. Development only. The fleet does not use it for the business. |
| Cohere | `COHERE_API_KEY` | https://dashboard.cohere.com/api-keys | Trial key. Development only. The fleet does not use it for the business. |
| Ollama (local) | none | n/a | A small local model for classification. It runs on your machine and needs 8 GB of RAM or more. |

## Read the terms

Free tiers change often. Some may use your prompts to improve their products. Some forbid commercial use. The links to each provider's terms are in `config/router/combos.yaml`.

The command `fleetctl router approve PROVIDER` makes you confirm that you read the terms before a provider turns on. The fleet rules forbid sending customer data to a provider that may train on prompts.

## Limits

The quota tracker reads router usage every 15 minutes and writes `budget.json`. At 80 percent of a daily free limit, the orchestrator slows down. The daily brief shows the limits that you are near.
