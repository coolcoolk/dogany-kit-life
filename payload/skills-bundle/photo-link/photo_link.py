#!/usr/bin/env python3
"""Local exchange state + captioned photo actions; MCP/Telegram are agent-owned.

stdout is an INTERNAL JSON plan. Execute its telegram actions in order. Visible
text comes only from approved copy. Every DB mutation invokes kit_life.sh.
"""
import argparse
import base64
import binascii
import fcntl
import json
import math
import re
from pathlib import Path
import subprocess
import sys
from urllib.parse import parse_qs, urlsplit

COPY = json.loads(Path(__file__).with_name("copy.json").read_text(encoding="utf-8"))
MAX_THUMB = 100 * 1024


def thumbnail(encoded):
    """Validate JPEG headers and bounds, preserving the MCP thumbnail bytes.

    No pixel decoding or image editing: the number is the Telegram photo caption.
    The phone tool owns JPEG encoding; this checks its advertised dimensions.
    """
    if not isinstance(encoded, str) or len(encoded) > ((MAX_THUMB + 2) // 3) * 4:
        raise ValueError("thumbnail exceeds 100 KiB")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("invalid thumbnail base64") from exc
    if len(raw) > MAX_THUMB:
        raise ValueError("thumbnail exceeds 100 KiB")
    if not raw.startswith(b"\xff\xd8") or not raw.endswith(b"\xff\xd9"):
        raise ValueError("thumbnail must be JPEG")
    offset, dimensions = 2, None
    while offset < len(raw) - 2:
        if raw[offset] != 0xff:
            break
        while offset < len(raw) and raw[offset] == 0xff:
            offset += 1
        if offset >= len(raw):
            break
        marker = raw[offset]
        offset += 1
        if marker in (0x00, 0xd8, 0xd9) or 0xd0 <= marker <= 0xd7:
            break
        if marker == 0x01:  # TEM has no length field.
            continue
        length = int.from_bytes(raw[offset:offset + 2], "big")
        if length < 2 or offset + length > len(raw) - 2:
            break
        if marker in (0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7,
                      0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf):
            if length < 8 or length != 8 + 3 * raw[offset + 7]:
                break
            height = int.from_bytes(raw[offset + 3:offset + 5], "big")
            width = int.from_bytes(raw[offset + 5:offset + 7], "big")
            if not (0 < width <= 320 and 0 < height <= 320):
                raise ValueError("thumbnail must be JPEG, <=320px")
            dimensions = (width, height)
        if marker == 0xda:  # Start of scan; entropy-coded pixels follow.
            if dimensions and length >= 6 and length == 6 + 2 * raw[offset + 2]:
                return raw
            break
        offset += length
    raise ValueError("invalid JPEG thumbnail headers")


def request(state):
    return {"tool": "find_photo_candidates", "arguments": state["image"]}


def offer(state):
    available = state["available"]
    telegram = []
    for i in available:
        telegram.append({"type": "photo", "path": state["candidates"][i - 1]["thumbnail_path"],
                         "caption": str(i)})
    if len(available) == 1:
        key = "confirm_one"
    elif available:
        key = "pick"
    else:
        key = "no_match"
    telegram.append({"type": "text", "text": COPY["approved"][key]})
    return {"status": state["status"], "telegram": telegram}


def begin(args):
    image = {"wait_s": 30}
    if args.image_path:
        path = Path(args.image_path).resolve(strict=True)
        if not path.is_file():
            raise ValueError("image_path must be a file")
        image["image_path"] = str(path)
    else:
        if not args.mime_type:
            raise ValueError("image_base64 requires mime_type")
        encoded = Path(args.image_base64_file).read_text(encoding="ascii").strip()
        if not base64.b64decode(encoded, validate=True):
            raise ValueError("empty image_base64")
        image.update(image_base64=encoded, mime_type=args.mime_type)
    state = {"phase": "result", "image": image, "note": Path(args.note_file).read_text(encoding="utf-8"),
             "record_id": args.record_id, "candidates": [], "available": [], "picked": None}
    return state, {"status": "request", "mcp": request(state), "telegram": []}


def receive(state, response, directory):
    if state["phase"] != "result":
        raise ValueError("no candidate request awaiting a result")
    status = response.get("status")
    if status not in ("matched", "no_match", "pending", "timed_out"):
        raise ValueError("unknown candidate status")
    state["status"] = status
    if status != "matched":
        state["phase"] = "unlock" if status in ("pending", "timed_out") else "no_match"
        key = "phone_locked" if state["phase"] == "unlock" else "no_match"
        return {"status": status, "telegram": [{"type": "text", "text": COPY["approved"][key]}]}
    candidates = response.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("matched requires candidates")
    ids = set()
    for candidate in candidates:
        asset = candidate.get("asset_id")
        distance = candidate.get("distance")
        if not isinstance(asset, str) or not asset.strip() or asset in ids:
            raise ValueError("candidate asset_id missing or duplicated")
        if isinstance(distance, bool) or not isinstance(distance, (float, int)) or not math.isfinite(distance) or distance < 0:
            raise ValueError("invalid candidate distance")
        # photo-match.v1.md: capture_time may be null (no time is invented).
        if candidate.get("capture_time") is not None and not isinstance(candidate["capture_time"], str):
            raise ValueError("candidate capture_time invalid")
        ids.add(asset)
    candidates = sorted(candidates, key=lambda item: item["distance"])
    # Validate the whole batch before creating any delivery files.
    images = [thumbnail(item["thumbnail_base64"]) for item in candidates]
    state["candidates"] = []
    for i, (candidate, raw) in enumerate(zip(candidates, images), 1):
        path = directory / ("%d.jpg" % i)
        path.write_bytes(raw)
        state["candidates"].append({"asset_id": candidate["asset_id"], "capture_time": candidate.get("capture_time"),
                                    "distance": candidate["distance"], "thumbnail_path": str(path)})
    state["available"] = list(range(1, len(candidates) + 1))
    state["phase"] = "choice"
    return offer(state)


def write_note(state, kit, verb, *pairs):
    if not kit:
        raise ValueError("mutation requires --kit /absolute/path/database/kit_life.sh")
    completed = subprocess.run(["bash", str(Path(kit).resolve(strict=True)), verb, *pairs],
                               capture_output=True, text=True, timeout=30)
    if completed.returncode:
        raise ValueError(completed.stderr.strip() or "kit photo verb failed")
    rid = completed.stdout.strip()
    if not rid.startswith("lk_") or len(rid.splitlines()) != 1:
        raise ValueError("kit photo verb returned an invalid record_id; inspect before retry")
    return rid


def results_sent(args):
    """Record an already-delivered filter_photos link; never perform delivery.

    Keep the exact filter arguments and the ordered IDs carried by the link.
    The caller invokes this only after successful delivery, in a fresh session.
    """
    query = json.loads(args.query_file.read_text(encoding="utf-8"))
    response = json.loads(args.response_file.read_text(encoding="utf-8"))
    if not isinstance(query, dict):
        raise ValueError("query file requires the structured filter_photos arguments")
    link = response.get("results_link")
    if not isinstance(link, str) or not link:
        raise ValueError("no results link was delivered")
    ids = []
    for photo in response["photos"]:
        value = photo.get("id")
        if (isinstance(value, str) and 8 <= len(value) <= 100 and
                re.fullmatch(r"[A-Za-z0-9-]{8,64}(?:/[A-Za-z0-9]{1,16}){0,3}", value) and value not in ids):
            if photo.get("source") != "photos" or photo.get("kind") != "photo":
                raise ValueError("results require Photos photo references")
            ids.append(value)
            if len(ids) == 60:
                break
    linked = parse_qs(urlsplit(link).fragment).get("ids", [])
    if len(linked) != 1 or linked[0].split(",") != ids or not ids:
        raise ValueError("results link does not match the returned ordered photo IDs")
    pairs = ["sent_at=" + args.sent_at, "channel=" + args.channel,
             "query=" + json.dumps(query, ensure_ascii=False),
             "photo_ids=" + json.dumps(ids), "link=" + link]
    if args.query_text is not None:
        pairs.append("query_text=" + args.query_text)
    if "total" in response:
        if type(response["total"]) is not int:
            raise ValueError("total requires an integer")
        pairs.append("total_matches=" + str(response["total"]))
    rid = write_note(None, args.kit, "photo-send-log", *pairs)
    return {"phase": "sent", "record_id": rid}, {"status": "sent", "record_id": rid, "telegram": []}


def reply(state, args):
    if args.unlocked:
        if state["phase"] != "unlock":
            raise ValueError("unlock retry requires pending or timed_out")
        state["phase"] = "result"
        return {"status": "request", "mcp": request(state), "telegram": []}
    if args.reject:
        if state["phase"] == "linked" and state["picked"] is not None:
            rejected = state["picked"]
        elif state["phase"] == "choice" and len(state["available"]) == 1:
            rejected = state["available"][0]
        else:
            raise ValueError("rejection requires a selected or sole offered photo")
        state["available"].remove(rejected)
        state["picked"] = None
        state["phase"] = "choice"
        if state["available"]:
            return offer(state)
        # Exhausted selection falls through to deletion of the incorrect memo.
    if args.none or (args.reject and not state["available"]):
        if state["phase"] not in ("choice", "linked", "no_match"):
            raise ValueError("no offer to decline")
        if state["record_id"]:
            write_note(state, args.kit, "photo-note-del", "record_id=" + state["record_id"])
        state["phase"] = "deleted"
        return {"status": "deleted", "record_id": state["record_id"],
                "telegram": [{"type": "text", "text": COPY["approved"]["no_match"]}]}
    if state["phase"] != "choice":
        raise ValueError("no photo choice awaiting an answer")
    if args.confirm:
        if len(state["available"]) != 1:
            raise ValueError("confirmation requires exactly one offered candidate")
        number = state["available"][0]
    else:
        number = args.choice
    if number not in state["available"]:
        raise ValueError("choice must be a currently offered photo number")
    candidate = state["candidates"][number - 1]
    pairs = ["source=photos", "kind=photo", "id=" + candidate["asset_id"], "note=" + state["note"]]
    verb = "photo-note-upd" if state["record_id"] else "photo-note-add"
    if state["record_id"]:
        pairs.insert(0, "record_id=" + state["record_id"])
    state["record_id"] = write_note(state, args.kit, verb, *pairs)
    state.update(phase="linked", picked=number)
    return {"status": "linked", "record_id": state["record_id"],
            "telegram": [{"type": "text", "text": COPY["approved"]["saved"]}]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("begin")
    start.add_argument("--note-file", required=True)
    image = start.add_mutually_exclusive_group(required=True)
    image.add_argument("--image-path")
    image.add_argument("--image-base64-file")
    start.add_argument("--mime-type")
    start.add_argument("--record-id")
    result = commands.add_parser("result")
    result.add_argument("--response-file", type=Path, required=True)
    answer = commands.add_parser("reply")
    choice = answer.add_mutually_exclusive_group(required=True)
    choice.add_argument("--choice", type=int)
    choice.add_argument("--confirm", action="store_true")
    choice.add_argument("--reject", action="store_true")
    choice.add_argument("--none", action="store_true")
    choice.add_argument("--unlocked", action="store_true")
    answer.add_argument("--kit")
    sent = commands.add_parser("results-sent", help="log an already-delivered filter_photos link")
    sent.add_argument("--query-file", type=Path, required=True)
    sent.add_argument("--response-file", type=Path, required=True)
    sent.add_argument("--channel", required=True)
    sent.add_argument("--sent-at", required=True)
    sent.add_argument("--query-text")
    sent.add_argument("--kit", required=True)
    args = parser.parse_args(argv)
    try:
        directory = args.session.resolve()
        if any(c in str(directory) for c in "\r\n"):
            raise ValueError("session path cannot contain line breaks")
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (directory / "lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            state_path = directory / "state.json"
            if args.command in ("begin", "results-sent"):
                if state_path.exists():
                    raise ValueError("session already exists; resume it or use a new photo-message session")
                state, output = begin(args) if args.command == "begin" else results_sent(args)
            else:
                state = json.loads(state_path.read_text(encoding="utf-8"))
                if args.command == "result":
                    output = receive(state, json.loads(args.response_file.read_text(encoding="utf-8")), directory)
                else:
                    output = reply(state, args)
            temporary = directory / "state.tmp"
            temporary.write_text(json.dumps(state, ensure_ascii=True), encoding="utf-8")
            temporary.replace(state_path)
        print(json.dumps(output, ensure_ascii=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, ImportError, subprocess.SubprocessError) as exc:
        print("photo-link: %s\nREMEDY: Inspect the exchange state and retry the failed step after fixing the cause." % exc,
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
