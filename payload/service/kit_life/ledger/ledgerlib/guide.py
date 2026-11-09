"""The per-card automation guide, rendered from DATA (issuers/*.json +
guides/ios-shortcut.json). Instructions, not questions: the onboarding asks
once whether to set it up; this is what the owner then follows.

A field the data marks unverified with no value is never filled in: the text
says what to look for and that the menu name was not checked.
"""
import json

from . import issuers, paths
from item_grouping import markdown_items


def shortcut_doc():
    return json.loads((paths.SERVICE_DIR / 'guides' / 'ios-shortcut.json').read_text(encoding='utf-8'))


def card_section(doc):
    g = doc['guide']
    name = doc['display_ko']
    lines = ['[%s \uacb0\uc81c \ubb38\uc790 \ucf1c\uae30]' % name]
    if g['menu_path']['value']:
        lines.append('- \uc571\uc5d0\uc11c: ' + ' \u2192 '.join(g['menu_path']['value']))
    else:
        lines.append("- %s \uc571\uc5d0\uc11c '\uce74\ub4dc\uc774\uc6a9\uc54c\ub9bc'\uc774\ub098 '\uacb0\uc81c \uc54c\ub9bc'\uc744 \ucc3e\uc544 \ubb38\uc790\ub85c \uc2e0\uccad\ud574\uc8fc\uc138\uc694. "
                     "(\uba54\ub274 \uc774\ub984\uc740 \uc81c\uac00 \ud655\uc778\ud558\uc9c0 \ubabb\ud588\uc5b4\uc694)" % name)
    if g['alt_route']['value']:
        lines.append('- \uc804\ud654\ub85c\ub3c4 \ub3fc\uc694: ' + g['alt_route']['value'])
    if g['fee']['value']:
        lines.append('- \uc694\uae08: ' + g['fee']['value'])
    else:
        lines.append('- \uc694\uae08: \uc2e0\uccad \ud654\uba74\uc5d0 \ub098\uc624\ub294 \uae08\uc561\uc744 \ud655\uc778\ud574\uc8fc\uc138\uc694. (\ubcf4\ud1b5 \uc6d4 \uba87\ubc31 \uc6d0\uc774\uc5d0\uc694)')
    return [lines[0], markdown_items(line.removeprefix('- ') for line in lines[1:])]


def render(issuer_keys, docs=None):
    docs = docs or issuers.load_all()
    sc = shortcut_doc()
    names = [docs[k]['display_ko'] for k in issuer_keys]
    out = []
    for k in issuer_keys:
        out += card_section(docs[k]) + ['']
    out += ['[\uc544\uc774\ud3f0 \ub2e8\ucd95\uc5b4\ub85c \uacb0\uc81c \ubb38\uc790 \u2192 \ub3c4\uac00\ub2c8 \uc571]', '\uc900\ube44: ' + sc['requires']]
    who = ', '.join(names)
    keywords = [issuers.shortcut_keyword(docs[k]) for k in issuer_keys]
    keyword = (', '.join("'%s'" % kw for kw in keywords if kw) if all(keywords)
               else '\ub0b4 \uacb0\uc81c \ubb38\uc790 \uccab \uc904\uc758 \uce74\ub4dc \uc774\ub984 \ubd80\ubd84')
    steps = []
    for n, st in enumerate(sc['steps'], 1):
        text = st['text'].format(issuer=who, keyword=keyword)
        if len(issuer_keys) > 1 and '{keyword}' in st['text']:
            text += ' [\uba54\uc2dc\uc9c0\uc5d0 \ud3ec\ud568]\uc5d0\ub294 \uae00\uc790\ub97c \ud558\ub098\ub9cc \ub123\uc744 \uc218 \uc788\uc5b4\uc11c, \uce74\ub4dc\ub9c8\ub2e4 \uc790\ub3d9\ud654\ub97c \ud558\ub098\uc529 \ub9cc\ub4e4\uc5b4\uc8fc\uc138\uc694. \ub098\uba38\uc9c0 \uc124\uc815\uc740 \ubaa8\ub450 \uac19\uc544\uc694.'
        steps.append('%d. %s' % (n, text.replace('\n', '\n   ')))
    out.append(markdown_items(steps))
    out += ['', '[\ud655\uc778] \uc124\uc815\ud558\uace0 \ub098\uc11c \uccab \uacb0\uc81c \ubb38\uc790\uac00 \ub4e4\uc5b4\uc624\uba74 \uc81c\uac00 "\uc798 \ub4e4\uc5b4\uc624\uace0 \uc788\uc5b4\uc694"\ub77c\uace0 \uc54c\ub824\ub4dc\ub9b4\uac8c\uc694. '
            '\uba70\uce60 \uc9c0\ub098\ub3c4 \uc548 \uc624\uba74 \ubb34\uc5c7\uc744 \uace0\uce58\uba74 \ub418\ub294\uc9c0 \uc9da\uc5b4\ub4dc\ub9b4\uac8c\uc694.']
    return '\n'.join(out)
