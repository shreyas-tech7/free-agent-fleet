# Example project

`STATE.md` shows the record that the fleet keeps for each project. The numbers in the front matter are the stage gates.
The gate values live in `config/stages.yaml`. The orchestrator may tune them and must log each change.

Only one project is active at a time. `fleetctl pivot SLUG` archives the active project in `projects/_graveyard/` and starts a new one.
