# place-view -- missing browser dependency

`route.sh` exit 4 means agent-browser is not installed (PATH and known
install locations were checked). The agent installs it with
`npm install -g agent-browser`, runs `agent-browser install`, then retries
the same route command. Do not tell the owner "can't fix" or ask the owner
to install the tool, change the address, or supply travel minutes.

The schedule resolver reports this as
`AGENT_BROWSER_MISSING<TAB><install instruction>` (exit 9), including
through kit_life.py's `appt-origin` lookup. This differs from
travel-resolve.sh's exit 4 (`MODE_UNKNOWN`), which still asks for the user's
travel mode. Other route failures keep their existing handling.
