#!/usr/bin/env python3
"""Onboarding start and completion-relay gate. No callback URL handling."""
import argparse
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(__file__))
import adapter as A

C1 = "\uad6c\uae00 \uce98\ub9b0\ub354\ub97c \uc5f0\uacb0\ud560\uac8c\uc694. \uc544\ub798 \ub9c1\ud06c\ub97c Safari\ub098 Chrome\uc5d0\uc11c \uc5f4\uace0, \uacc4\uc815\uc744 \uace0\ub978 \ub2e4\uc74c '\ud5c8\uc6a9'\uc744 \ub20c\ub7ec \uc8fc\uc138\uc694."
C2 = "\uc911\uac04\uc5d0 '\ud655\uc778\ub418\uc9c0 \uc54a\uc740 \uc571' \ud654\uba74\uc774 \ub098\uc62c \uc218 \uc788\uc5b4\uc694. Dogany\uac00 \uc544\uc9c1 \uad6c\uae00 \uc2ec\uc0ac \uc911\uc774\ub77c \uadf8\ub798\uc694. '\uace0\uae09' \u2192 'Dogany(\uc73c)\ub85c \uc774\ub3d9'\uc744 \ub204\ub974\uba74 \uc774\uc5b4\uc11c \ud560 \uc218 \uc788\uc5b4\uc694."
C4 = "\ud3f0\uc774\ub098 \uc11c\ubc84\uc5d0\uc11c \uc5f0\uacb0\ud558\uc2e4 \ub54c\ub294 \ub9c1\ud06c\ub97c \uae38\uac8c \ub20c\ub7ec 'Safari\ub85c \uc5f4\uae30'(\uc548\ub4dc\ub85c\uc774\ub4dc\ub294 'Chrome\uc73c\ub85c \uc5f4\uae30')\ub97c \uace8\ub77c \uc8fc\uc138\uc694. \ud5c8\uc6a9\uc744 \ub204\ub974\uba74 '\uc5f0\uacb0\ud560 \uc218 \uc5c6\uc74c' \ud654\uba74\uc774 \ub728\ub294\ub370, \uc815\uc0c1\uc774\uc5d0\uc694. \uadf8 \ud654\uba74 \uc704\ucabd \uc8fc\uc18c\ub97c \ud1b5\uc9f8\ub85c \ubcf5\uc0ac\ud574\uc11c \uc5ec\uae30\uc5d0 \ubd99\uc5ec \uc8fc\uc138\uc694."


def start(origin):
    # The origin names the instance, never its absolute filesystem path.
    if (not re.fullmatch(r"[A-Za-z0-9_.-]+:-?[0-9]+", origin) or
            origin.split(":")[0] != os.path.basename(A._INSTANCE_ROOT)):
        return {"ok": False, "class": "flow_unknown"}
    from service import calendar
    status = calendar.status(A._INSTANCE_ROOT)
    target = status.get("target") or status.get("account_key") or ""
    cmd = [sys.executable, "-m", "service.google", "connect", "start", "--scopes",
           "calendar", "--origin", origin, "--json"]
    if target.startswith("google:"):
        cmd += ["--account", target.split(":", 1)[1]]
    env = dict(os.environ, PROJECT_ROOT=A._INSTANCE_ROOT)
    env["PYTHONPATH"] = os.pathsep.join((A._INSTANCE_ROOT,
        os.path.join(A._INSTANCE_ROOT, ".dogany-consume"), env.get("PYTHONPATH", "")))
    # The framework owns PKCE, listener, credentials and completion relay.
    result = subprocess.run(cmd, capture_output=True, text=True,
                            env=env, cwd=A._INSTANCE_ROOT)
    try:
        data = json.loads(result.stdout)
    except ValueError:
        return {"ok": False, "class": "internal"}
    if not data.get("url"):
        return {"ok": False, "class": data.get("class", "internal")}
    return {"ok": True, "flow_id": data["flow_id"], "url": data["url"],
            "copy": [C1, C2, C4], "waiting_for": "google-connect-result"}


def completed(relay, origin):
    """Only the trusted bridge relay resumes calendar-name / engrave steps."""
    from service import calendar
    status = calendar.status(A._INSTANCE_ROOT)
    # Relay format is the frozen framework {class,sub} completion result.
    if (origin.split(":")[0] != os.path.basename(A._INSTANCE_ROOT) or
            relay.get("origin") != origin):
        return {"ok": False, "class": "flow_unknown"}
    if (relay.get("class") not in (None, "ok") or
            not relay.get("sub") or not status.get("connected") or
            status.get("health") or status.get("account_key") != "google:" + relay["sub"] or
            not {"calendar"}.issubset(status.get("granted") or [])):
        return {"ok": False, "class": (relay.get("class") if relay.get("class") not in (None, "ok") else "needs_consent")}
    return {"ok": True, "next": "calendar-name"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("start", "complete", "copy"))
    parser.add_argument("--origin")
    parser.add_argument("--relay-json")
    args = parser.parse_args()
    if args.command == "start":
        if not args.origin:
            parser.error("start requires --origin instance:chat")
        result = start(args.origin)
    elif args.command == "complete":
        if not args.relay_json or not args.origin:
            parser.error("complete requires --origin and --relay-json from the trusted bridge relay")
        result = completed(json.loads(args.relay_json), args.origin)
    else:
        print("\n\n".join((C1, C2, C4)))
        return
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
