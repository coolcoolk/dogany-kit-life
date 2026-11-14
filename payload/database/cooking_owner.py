"""Owner-turn receipts written by UserPromptSubmit, never by a cooking CLI argument.

The receipt is bound to the bridge request. Missing or stale evidence fails
closed at the public CLI boundary; internal domain calls remain testable.
"""
import json
import os
from pathlib import Path
import re


def owner_words(prompt):
    prompt = re.sub(r'\[bridge:outbound-context\].*?\[/bridge:outbound-context\]\s*', '', prompt, flags=re.S).strip()
    if prompt.startswith('\uc0ac\uc6a9\uc790\uac00 \uc0ac\uc9c4\uc744 \ubcf4\ub0c8\uc2b5\ub2c8\ub2e4.') and '\uc0ac\uc6a9\uc790 \ucea1\uc158:' in prompt:
        return prompt.split('\uc0ac\uc6a9\uc790 \ucea1\uc158:', 1)[1].strip()
    return prompt


def identity(ctx):
    return {k: ctx.get(k) for k in ('request_id', 'message_id', 'ts', 'source', 'callback_data')}


def record(root, prompt):
    bot = Path(root) / '.telegram_bot'
    try:
        ctx = json.loads((bot / 'inbound-context.json').read_text())
    except (OSError, ValueError):
        ctx = {}
    # No bridge identity means no trustworthy receipt can be issued.
    if not ctx.get('request_id'):
        return
    bot.mkdir(parents=True, exist_ok=True)
    tmp = bot / 'cooking-owner-turn.json.tmp'
    tmp.write_text(json.dumps(dict(context=identity(ctx), words=owner_words(prompt)), ensure_ascii=False))
    os.replace(tmp, bot / 'cooking-owner-turn.json')


def latest(K):
    import activity_session as frame
    try:
        receipt = json.loads((Path(K._instance_root()) / '.telegram_bot/cooking-owner-turn.json').read_text())
    except (OSError, ValueError):
        return None
    ctx = frame.inbound(K)
    if not ctx.get('request_id') or receipt.get('context') != identity(ctx):
        return None
    return receipt.get('words')


def require(K, text):
    said = latest(K)
    if said is None or owner_words(text) != said:
        raise ValueError('owner provenance required: use the latest owner message verbatim; never invent progress wording')
    return said
