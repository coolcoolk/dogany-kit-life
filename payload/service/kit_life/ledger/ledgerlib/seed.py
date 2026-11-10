"""Register this module's intake sources and money match rule as DATA.

Sources (all module_ledger, resource money):
  csv:banksalad      Banksalad export file (weekly sweep / backfill)
  screenshot:sweep   rows the session model read off a spend-list screenshot
  sms:<issuer>       card SMS the owner's iOS Shortcut hands to the Dogany
                     app (read from the transporter receiver) -- one per
                     card issuer the owner automated

Idempotent and non-destructive by construction:
  - an existing source is only touched to flip `enabled` when the caller
    says exactly which sms sources end enabled (onboarding apply);
  - the match rule is written only when absent or different, and never over a
    rule another module owns (that is reported, not overwritten).
"""
import json

from . import kit

CSV_SOURCE = 'csv:banksalad'
SHOT_SOURCE = 'screenshot:sweep'
ALWAYS_ON = (CSV_SOURCE, SHOT_SOURCE)


def sms_source(issuer):
    return 'sms:' + issuer


def issuer_of(source_key):
    return source_key.split(':', 1)[1] if source_key.startswith('sms:') else None


def sources_by_key():
    return {s['key']: s for s in kit.source_list()}


def ensure(sms_issuers=(), enable_sms=None):
    """sms_issuers: register these sms sources if missing.
    enable_sms: None = leave every existing sms source as it is;
                a set = exactly these issuers' sources end enabled=1 and every
                other module_ledger sms source enabled=0 (onboarding apply)."""
    actions = []
    have = sources_by_key()
    for key in ALWAYS_ON:
        if key not in have:
            kit.source_set(key, True)
            actions.append('source+ ' + key)
    for iss in sms_issuers:
        key = sms_source(iss)
        if key not in have:
            want = enable_sms is not None and iss in enable_sms
            kit.source_set(key, want)
            actions.append('source+ %s enabled=%d' % (key, want))
    if enable_sms is not None:
        have = sources_by_key()
        for key, s in sorted(have.items()):
            if not key.startswith('sms:') or s['owning_module'] != kit.MODULE:
                continue
            want = issuer_of(key) in enable_sms
            if bool(s['enabled']) != want:
                kit.source_set(key, want, provenance=s['value_origin'])
                actions.append('source~ %s enabled=%d' % (key, want))
    rules = {r['resource']: r for r in kit.rule_list()}
    cur = rules.get(kit.RESOURCE)
    if cur is None:
        kit.rule_set()
        actions.append('rule+ money')
    elif cur['owning_module'] != kit.MODULE:
        actions.append('rule! money owned by %s -- left alone' % cur['owning_module'])
    elif json.loads(cur['rule_json']) != kit.MATCH_RULE or not cur['enabled']:
        kit.rule_set()
        actions.append('rule~ money')
    return actions


def enabled_sms_sources():
    return [s for s in kit.source_list()
            if s['key'].startswith('sms:') and s['owning_module'] == kit.MODULE and s['enabled']]
