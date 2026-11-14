"""Reminder sending scope; independent of the leader's broad schedule view.

Read only. The kit resolver owns the domain; scheduler state gates its handoff.
Shared card keys remain global for dedup; sender identity scopes edits and taps.
"""
import os
from pathlib import Path
import re
import subprocess
import sys


def instance_root(K):
    # abspath preserves the instance-facing database symlink; realpath would
    # strand config reads inside database/.gen/pack/kit_life/<version>/.
    return os.environ.get("LIFEKIT_INSTANCE_ROOT", "").strip() or os.path.dirname(
        os.path.abspath(K.SCRIPT_DIR))


def instance_agent(K):
    value = K._instance_conf_read(instance_root(K)).get(
        "DOGANY_AGENT_NAME", "").strip().lower()
    return value if K._AGENT_SLUG_RE.fullmatch(value) else None


def _health_reminder_active(K, health):
    """Verify the pack's loaded, health-scoped poller without changing it.

    The pack currently activates a macOS remind-5min LaunchAgent. Inspect
    loaded arguments, not a shipped plist or an installation marker: a file
    alone cannot deliver. A waiting/not-running periodic job is still active.
    Unknown hosts, missing members and failed probes keep the life agent's fallback.
    """
    if sys.platform != "darwin" or not K._AGENT_SLUG_RE.fullmatch(health):
        return False
    _, roots = K._health_crew_members()
    members = [Path(root) for root in roots
               if K._instance_conf_read(root).get("DOGANY_AGENT_NAME", "").strip().lower()
               == health]
    if len(members) != 1:
        return False
    root = members[0]
    label = "com.telegram-skill-bot.%s.remind-5min" % health
    try:
        result = subprocess.run(
            ["launchctl", "print", "gui/%s/%s" % (os.getuid(), label)],
            capture_output=True, text=True, timeout=3, check=False)
        if result.returncode:
            return False
        match = re.search(r"\n\s*arguments = \{\n(.*?)\n\s*\}",
                          result.stdout, re.DOTALL)
        if not match:
            return False
        args = [line.strip() for line in match[1].splitlines() if line.strip()]
        # The framework cron guard precedes the actual command with '--'.
        if args[:2] == ["/bin/bash", str(root / "routines/cron-guard.sh")]:
            args = args[args.index("--") + 1:]
        if args[:1] == ["/bin/bash"]:
            args = args[1:]
        if args == [str(root / "routines/remind.sh"), "--owner", "health"]:
            return True
        wrapper = root / "routines/bundle/health-remind.sh"
        if args != [str(wrapper)]:
            return False
        # This is the pack-owned wrapper contract, not an unrestricted poller
        # that happens to use the same label. Never execute it to find out.
        return ('exec /bin/bash "$AGENT_DIR/routines/remind.sh" --owner health "$@"'
                in wrapper.read_text().splitlines())
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


class NotificationScope:
    def __init__(self, K, conn, excluded=(), owner=None):
        self.agent = instance_agent(K)
        self.role = K._instance_conf_read(instance_root(K)).get(
            "DOGANY_AGENT_CLASS", "main").strip().lower()
        self.health = K._health_agent(conn).lower()
        self.health_tokens = {self.health, "health", K._HEALTH_DOMAIN_AGENT_RETIRED}
        recorded = K._health_owner_recorded(conn)
        if recorded and recorded.lower() != "ag":
            self.health_tokens.add(recorded.lower())
        self.excluded = set(excluded)
        self.owner = owner
        self.health_fallback = (self.agent == "ag" and self.role != "domain"
                                and owner is None and self.health != "ag"
                                and not _health_reminder_active(K, self.health))
        self.conn = conn
        self.cache = {}

    def anchor_owner(self, block_id):
        """Follow derived_from recursively, including helpers owned by the life agent.

        Missing anchors/cycles are invalid and cannot safely be sent by either
        instance. No write or repair is attempted by the reminder reader.
        """
        if block_id in self.cache:
            return self.cache[block_id]
        seen, current = set(), block_id
        while current not in seen:
            seen.add(current)
            row = self.conn.execute(
                "SELECT owning_agent, derived_from FROM timeblock WHERE ulid=? OR id=?",
                (current, current)).fetchone()
            if row is None:
                break
            if row[1] is None:
                result = (row[0] or "").strip().lower()
                self.cache[block_id] = result
                return result
            current = row[1]
        self.cache[block_id] = None
        return None

    def accepts(self, owner, derived_from=None):
        token = (owner or "").strip().lower()
        if derived_from is not None:
            token = self.anchor_owner(derived_from)
            if token is None:
                return False
        if token in self.excluded:
            return False
        health = token in self.health_tokens
        if self.owner == "health" and not health:
            return False
        if health:
            return self.agent == self.health or self.health_fallback or (
                self.agent is None and self.health == "ag" and self.owner is None)
        if self.owner == "health":
            return False
        if self.role == "domain":
            return bool(self.agent) and token == self.agent
        return True  # preserve main's non-health alerts, including other owners


def card_sender(card):
    # Pre-routing cards were posted by the life agent's only running poller. Their row's
    # domain owner is NOT their Telegram sender. IDs are local to each bot.
    return card.get("sending_agent") or "ag"


def owns_card(K, card):
    agent = instance_agent(K)
    return bool(agent) and card_sender(card) == agent
