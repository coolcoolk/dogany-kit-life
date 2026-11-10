#!/usr/bin/env python3
"""Measure one complete directed course with the kit map driver, read-only.

usage: outing_routes.py plan.json measured.json
Input: outing_course.py schema, stops already in a proposed geographic order.
Output: the same plan with measured legs and raw route_receipts; never a DB write.
No output file is created on failure. Every browser command is foreground.
"""
import copy
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlsplit

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
HOUR = '\uc2dc\uac04'
MINUTE = '\ubd84'
SECTIONS = {'car': '\uc790\ub3d9\ucc28', 'transit': '\ub300\uc911\uad50\ud1b5',
            'walk': '\ub3c4\ubcf4'}


def parse_receipt(raw, mode):
    urls = re.findall(r'^MAP-URL: (https://\S+)$', raw, re.M)
    if len(urls) != 1:
        raise ValueError('map did not commit both endpoints')
    url = urlsplit(urls[0])
    ends = url.path.split('/directions/', 1)[-1].split('/')
    if url.hostname != 'map.naver.com' or '/directions/' not in url.path or len(ends) < 2 or any(e == '-' for e in ends[:2]):
        raise ValueError('invalid directions receipt')
    marker = '=== ' + SECTIONS[mode] + ' ==='
    if marker not in raw:
        raise ValueError('map has no requested mode section')
    section = raw.split(marker, 1)[1].split('===', 1)[0]
    # The first displayed recommendation, not the first route with a minute
    # suffix: Naver also returns exact-hour routes without a minute component.
    line = next((s.strip() for s in section.splitlines() if s.strip()), '')
    match = re.search(r'(?:(\d+)\s*' + HOUR + r'\s*)?(?:(\d+)\s*' + MINUTE + ')', line)
    if not match:
        match = re.search(r'(\d+)\s*' + HOUR, line)
        if not match:
            raise ValueError('map recommendation has no duration')
        minutes = int(match[1]) * 60
    else:
        minutes = int(match[1] or 0) * 60 + int(match[2])
    return minutes, urls[0], line


def measure(plan, runner=subprocess.run):
    mode = plan.get('mode', 'car')
    if mode not in SECTIONS:
        raise ValueError('unsupported travel mode')
    nodes = [plan['origin']] + plan['stops'] + [plan['origin']]
    if not plan['stops'] or any(not n.get('address') for n in nodes):
        raise ValueError('every endpoint needs its existing or verified address')
    result = copy.deepcopy(plan)
    result['legs'], result['route_receipts'] = [], []
    route = HERE.parent / 'place-view/route.sh'
    for index, (a, b) in enumerate(zip(nodes, nodes[1:])):
        cmd = ['bash', str(route), a['address'], b['address'], mode]
        env = dict(os.environ, ROUTE_KEEP_BROWSER='1' if index < len(nodes) - 2 else '0')
        proc = runner(cmd, capture_output=True, text=True, timeout=120, env=env)
        if proc.returncode:
            raise ValueError('route %s -> %s failed: %s' % (
                a['id'], b['id'], proc.stderr.strip()))
        minutes, evidence, line = parse_receipt(proc.stdout, mode)
        result['legs'].append(dict(**{'from': a['id'], 'to': b['id']},
                                   minutes=minutes, source='route', evidence=evidence))
        result['route_receipts'].append(dict(command=cmd, stdout=proc.stdout,
                                             recommendation=line))
    return result


def main(argv):
    if len(argv) != 3 or Path(argv[1]).resolve() == Path(argv[2]).resolve():
        print(__doc__, file=sys.stderr)
        return 2
    try:
        from outing_course import load_plan
        measured = measure(load_plan(argv[1]))
        Path(argv[2]).write_text(json.dumps(measured, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps(dict(measured_legs=len(measured['legs']), output=argv[2])))
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print('OUTING_ROUTE_BLOCKED: ' + str(error), file=sys.stderr)
        return 3


if __name__ == '__main__':
    sys.exit(main(sys.argv))
