#!/usr/bin/env python3
"""Read-only capture routing; does not read DB, save, schedule or send itself."""
import json
import re
import os
from pathlib import Path
import sys




def context(prompt):
    if re.fullmatch(r'(?:opt:)?contract (get|cost|edit|reminder|proof|rearm) \d+ \d+', prompt.strip()):
        return ('Run service/kit_life/contracts dispatch --data with {"token": the exact prompt}. '
                'Relay its card text and vertical buttons literally. For a stale card relay the current card. '
                'For edit/reminder replies, use returned reply.data id/revision with update --data '
                '{id,revision,facts:{only corrected fields}}. Never apply a bare label to another card.')
    if not re.search(r'[/\\]\.telegram_bot[/\\]images[/\\]', prompt):
        return ''
    return ('If the attachment is a contract, bill, receipt or warranty (not an activity-completion photo), follow contract/warranty capture: read service/kit_life/contracts.md now. Read visible attachments '
            'and stated facts, then run contracts list before matching. Capture partial facts immediately '
            'via service/kit_life/contracts capture --data JSON; never wait for missing terms. '
            'A provider is not an identity. Distinguish lines/devices before updating. '
            'Use source_ref from the original attachment content digest (or channel/message id), '
            'plus source_slot per subject; reuse id for a later correction. '
            'Warranty is kind:warranty, linked to the existing owned_id after ordinary purchase capture. '
            'A receipt alone establishes no warranty length. Never create a payment for a contract. '
            'Relay returned card A text/buttons literally, and disclose any enrichment_errors in owner language. '
            'Proof is stored, but chat resend needs the framework handoff; never promise a resend. '
            'In a correction reply use update with the id/revision of the bound card and only corrected facts.')


def main():
    try:
        payload = json.load(sys.stdin)
        prompt = payload.get('prompt', '')
        root = Path(os.environ.get('DOGANY_INSTANCE_ROOT') or Path(__file__).absolute().parents[2])
        try:
            inbound = json.loads((root / '.telegram_bot/inbound-context.json').read_text())
        except (OSError, ValueError):
            inbound = {}
        if isinstance(inbound, dict) and inbound.get('source') == 'callback':
            token = inbound.get('callback_data')
            if isinstance(token, str) and token.startswith('opt:contract '):
                prompt = token
        if isinstance(prompt, str):
            out = context(prompt)
            if out:
                print(out)
    except (ValueError, TypeError, AttributeError):
        return


if __name__ == '__main__':
    main()
