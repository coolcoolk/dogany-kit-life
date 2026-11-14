"""The only door to the ledger data: kit_life.sh verbs.

This module owns no table and issues no write SQL. Writes go through the
kit 2.28.0 intake verbs (source-set, match-rule-set, intake-add,
intake-resolve, import-csv, ledger-archive-reset) and the existing payline
verbs. Reads use the kit's dedicated read verbs; this module never queries its
tables directly.

G1 (force-pending) is isolated in `intake_add_held` behind ONE constant,
FORCE_PENDING_FLAG.
"""
import hashlib
import json
import os
import subprocess

from . import paths

MODULE = 'module_ledger'
RESOURCE = 'money'

# The module's money match policy, registered as DATA (kit rule format v1).
MATCH_RULE = {
    'version': 1,
    'exact': ['amount', 'date', 'entity_id', 'merchant', 'unit', 'kind'],
    'partial': ['amount', 'date'],
    'date_window_guard': 'dup_window',
    'merchant_normalization': 'nfkc_casefold_alnum',
}


class KitError(Exception):
    def __init__(self, verb, code, message):
        super().__init__('%s: %s' % (verb, message))
        self.verb, self.code, self.message = verb, code, message


# Read verbs whose answer cannot change until this process writes: memoized
# per process (one `onboarding next` renders the card from the same reads
# several times). Any other verb is a write and clears the memo.
CACHED_READS = ('method-list', 'fixed-list', 'ledger-records', 'methods-seen', 'fixed-detect', 'flow-picture',
                'sub-tiers', 'spend-habits', 'recurring-summary',
                'source-list', 'match-rule-list', 'installment-detect', 'goal-list')
_memo = {}


def run(verb, *args, parse=True, timeout=60, input=None):
    key = (verb, tuple(str(a) for a in args), parse)
    if verb in CACHED_READS:
        if key in _memo:
            return json.loads(json.dumps(_memo[key])) if parse else _memo[key]
    else:
        _memo.clear()
    out = _run(verb, *args, parse=parse, timeout=timeout, input=input)
    if verb in CACHED_READS:
        _memo[key] = out
        return json.loads(json.dumps(out)) if parse else out
    return out


def _run(verb, *args, parse=True, timeout=60, input=None):
    cmd = ['bash', str(paths.kit_sh()), verb] + [str(a) for a in args]
    # The shared crew DB cannot identify the calling agent's memory directory.
    # Carry the service instance explicitly; never scan another agent's notes.
    env = dict(os.environ, LEDGER_ROOT=str(paths.root()))
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, input=input, env=env)
    if p.returncode != 0:
        msg = (p.stdout or '').strip() or (p.stderr or '').strip()
        try:
            msg = json.loads(msg).get('error', msg)
        except (ValueError, AttributeError):
            pass
        raise KitError(verb, p.returncode, msg)
    if not parse:
        return p.stdout
    return json.loads(p.stdout)


# ---- intake -------------------------------------------------------------

def source_list():
    return run('source-list', '--resource', RESOURCE)


def source_set(key, enabled, provenance='measured'):
    return run('source-set', key, '--resource', RESOURCE, '--module', MODULE,
               '--provenance', provenance, '--enabled', 1 if enabled else 0)


def rule_list():
    return run('match-rule-list')


def manage_state():
    """(state, reason) of the ledger's management lens (kit manage_gate):
    ('off', 'declined') when the owner said no to managing it."""
    row = run('manage-state', MODULE, '--json')[0]
    return row['state'], row['reason']


def rule_set(rule=None):
    return run('match-rule-set', RESOURCE, '--module', MODULE,
               '--rule', json.dumps(rule or MATCH_RULE, sort_keys=True))


def intake_add(source, ref, data, raw=None, extra=()):
    args = ['--source', source, '--source-ref', ref,
            '--data', json.dumps(data, ensure_ascii=False, sort_keys=True)]
    if raw is not None:
        args += ['--raw', raw]
    return run('intake-add', *(args + list(extra)))


# G1 adapter: model-read items always wait for owner review.
FORCE_PENDING_FLAG = '--hold'


def intake_add_held(source, ref, data, raw=None):
    """Stage a model reading as pending, regardless of auto-accept settings."""
    return intake_add(source, ref, data, raw=raw, extra=[FORCE_PENDING_FLAG])


def intake_list(pending=False):
    args = ['--resource', RESOURCE]
    if pending:
        args.insert(0, '--pending')
    return run('intake-list', *args)


def intake_resolve(item_id, action, block_id=None, spend_id=None, reversal_of=None, new=False, scope=None):
    args = [item_id, action]
    if block_id is not None:
        args += ['--block-id', block_id]
    if spend_id is not None:
        args += ['--spend-id', spend_id]
    if reversal_of is not None:
        args += ['--reversal-of', reversal_of]
    if new:
        args.append('--new')
    if scope is not None:
        args += ['--scope', scope]
    return run('intake-resolve', *args)


def import_csv(source, path, password=None):
    """A Banksalad export: CSV, xlsx, or the mailed zip (2.46.0). The
    password goes to the kit on stdin for this call only -- never an
    argument (process list), never stored, never logged."""
    if password is None:
        return run('import-csv', '--source', source, '--file', path, timeout=600)
    return run('import-csv', '--source', source, '--file', path, '--password-stdin', timeout=600,
               input=password + '\n')


IMPORT_ERRORS = (('the file has no payments', 'empty'), ('password-needed', 'password'), ('password-wrong', 'password_wrong'),
                 ('zip-encryption-unsupported', 'locked'), ('xlsx-locked', 'locked'))


def import_error(e):
    """A kit import refusal -> the owner-facing reason token."""
    msg = getattr(e, 'message', '') or str(e)
    for token, reason in IMPORT_ERRORS:
        if msg.startswith(token):
            return reason
    return 'format'


def archive_reset(expected_count):
    """DESTRUCTIVE. Callers must hold an explicit owner yes for exactly
    `expected_count` rows (onboarding Q-archive). The kit refuses unless the
    count equals the live spend_entry count, so a stale yes cannot wipe a
    ledger that grew since it was asked."""
    return run('ledger-archive-reset', '--confirm', int(expected_count), timeout=600)


def archive_count():
    """G2: non-mutating count for the explicit archive decision."""
    return int(run('ledger-archive-reset', '--dry-run')['count'])


# ---- ledger v6 (migration 075): the service verbs the flow and the agent need ----
# The agent never reads the database: import / find / reconcile / stop / reset are verbs.

def ledger_import(path, password=None, since=None, until=None):
    """A bank export lands as classified rows (identity + bucket reconcile against the ledger, streams)."""
    args = [path]
    if since is not None:
        args += ['--since', str(since)]
    if until is not None:
        args += ['--until', str(until)]
    if password is None:
        return run('ledger-import', *args, timeout=900)
    return run('ledger-import', *args, '--password-stdin', timeout=900, input=password + '\n')


def ledger_diagnosis(month=None, roster_only=False):
    return run('ledger-diagnosis', *(['--month', month] if month else []),
               *(['--roster-only'] if roster_only else []), '--json', timeout=300)


def ledger_state():
    return run('ledger-state')


def ledger_find(text, month=None, raw=False, path=None):
    args = [text] + (['--month', month] if month else []) + (['--raw', '--file', path] if raw and path else [])
    return run('ledger-find', *args)


def ledger_reset(dry_run=True):
    return run('ledger-reset', '--dry-run' if dry_run else '--yes', timeout=900)


def stream_stop(stream_id, said=None):
    return run('stream-stop', stream_id, *(['--said', said] if said else []))


def stream_keep(stream_id, keep):
    return run('stream-keep', stream_id, keep)


def stream_set(stream_id, field, value):
    return run('stream-set', stream_id, field, value)


def stream_link(stream_id, kind, ref):
    return run('stream-link', stream_id, kind, ref)


def card_answer(spend_id, answer):
    return run('ledger-card-answer', spend_id, answer)


# ---- payline / guards (pre-2.28 verbs, text output) ---------------------

def guard_value(key, scope='global'):
    out = run('guard-get', key, scope, parse=False).strip().split('\t')
    # out: key scope value unit enforcement inviolable
    try:
        return int(out[2])
    except (IndexError, ValueError):
        return None


def spend_unknown():
    return run('spend-unknown', parse=False)


def method_alias_set(method_id, aliases):
    return run('method-alias-add', method_id, *aliases)


def spend_row_count():
    return archive_count()


def pay_methods(canonical=False):
    return run('method-list', '--json', *(['--canonical'] if canonical else []))


def spend_by_method(since):
    """G11 remains open: kit 2.28.0 has no spend-by-method read verb."""
    raise KitError('spend-by-method', 2, 'kit has no spend-by-method read verb')


def block_for_spend(spend_id):
    """G4 bridge for callers that need a block id from a spend id."""
    return int(run('spend-block', '--spend-id', int(spend_id))['block_id'])


def spend_for_block(block_id):
    """G4 bridge the other way: the spend row id behind a landing block."""
    return int(run('spend-block', '--block-id', int(block_id))['spend_id'])


def spend_category(spend_id, date):
    """Category key of one spend row ('(unclassified)' when none), via spend-find."""
    for line in run('spend-find', date, '--all', parse=False).splitlines():
        cols = line.split('\t')
        if len(cols) >= 4 and cols[0] == str(spend_id):
            return cols[3]
    return None


def spend_upd(spend_id, **fields):
    """Partial update of one spend row (note / category ...), kit spend-upd."""
    return run('spend-upd', int(spend_id), *['%s=%s' % kv for kv in sorted(fields.items())], parse=False)


# ---- shared ingest contract -----------------------------------------------

def sms_consent(enabled):
    return run('ingest-consent', 'shortcut', 'card_sms', '--module', MODULE,
               '--json', *([] if enabled else ['--off']))


def sms_cursor():
    try:
        return run('ingest-get', '--source', 'shortcut', '--kind', 'card_sms', '--json')
    except KitError as e:
        # The cursor verb returns its JSON even with REFUSED (6).
        if e.code == 6:
            return json.loads(e.message)
        raise


def sms_ref_get(row):
    return run('ingest-get', '--source', row['source'], '--kind', row['kind'],
               '--id', row['id'], '--json')


def sms_check(row):
    return run('ingest-check', '--source', row['source'], '--kind', row['kind'],
               '--occurred-at', row['occurred_at'], parse=False)


def sms_ref_put(row, item_id):
    return run('ingest-put', '--source', row['source'], '--kind', row['kind'],
               '--id', row['id'], '--occurred-at', row['occurred_at'],
               '--target-table', 'intake_item', '--target-id', item_id, parse=False)


# ---- onboarding answers (074 consult anchor) --------------------------------
# Every onboarding answer lands in the kit with --anchor ledger:<question>.
# The purpose is a why, the goal and its amount one ledger_goal row (the
# monthly budget = the monthly_spend target), the watched categories
# ledger_preference rows. Answers held outside the kit (sweep time, archive,
# card SMS, sender) are anchored as pack_file pointers into onboarding.json.

ANCHOR_DOMAIN = 'ledger'
GOAL_DOMAIN = 'spend'           # goal_metric_source.domain of monthly_spend
WATCH_TOPIC = 'ledger_watch'


def _anchor(question, said):
    out = ['--anchor', '%s:%s' % (ANCHOR_DOMAIN, question)]
    if said:
        out += ['--said', said]
    return out


def _goal_id(text):
    """First goal line of a goal-add / goal-set / EXISTS output -> id."""
    for line in (text or '').splitlines():
        head = line.split('\t', 1)[0]
        if head.isdigit():
            return int(head)
    raise KitError('goal', 2, 'no goal line in %r' % (text,))


def why_add(text, said=None, label=None):
    """The owner's why (the goal answer: their own words, or the tapped goal's
    why line). The same words again -> the existing why (EXISTS), still
    anchored, as the goal question."""
    args = ['--text', text, '--domain', GOAL_DOMAIN, '--scope', 'module', '--json']
    if label:
        args += ['--label', label]
    try:
        out = run('why-add', *(args + _anchor('goal', said or text)), parse=False)
    except KitError as e:
        if e.code != 3:
            raise
        return int(e.message.splitlines()[1].split('\t', 1)[0])
    return int(json.loads(out)['id'])


def goal_put(goal_id, title, metric, direction, detail, said, new=False):
    """One ledger goal row for the onboarding goal. goal_id: the row this
    onboarding wrote before (re-run) or None. A live same-strand goal the kit
    reports (EXISTS) is revised instead of duplicated. new=True: one goal per lever (ledger v6) -- the kit's
    plurality rule (goal-add --new) so picking three levers leaves three goals, not one rewritten three times."""
    fields = ['--title', title, '--metric', metric or '', '--dir', direction,
              '--detail', json.dumps(detail, ensure_ascii=False, sort_keys=True)]
    if goal_id is None and new:
        return _goal_id(run('goal-add', '--layer', 'mid', '--domain', GOAL_DOMAIN, '--new',
                            *(fields + _anchor('goal', said)), parse=False))
    if goal_id is None:
        try:
            return _goal_id(run('goal-add', '--layer', 'mid', '--domain', GOAL_DOMAIN,
                                *(fields + _anchor('goal', said)), parse=False))
        except KitError as e:
            if e.code != 3:
                raise
            goal_id = _goal_id(e.message.split('\n', 1)[1])
    return _goal_id(run('goal-set', goal_id, '--target', '', *(fields + _anchor('goal', said)),
                        parse=False))


def goal_amount(goal_id, amount, said):
    return _goal_id(run('goal-set', goal_id, '--target', int(amount),
                        *_anchor('amount', said), parse=False))


def goal_live(goal_id):
    if goal_id is None:
        return False
    rows = run('goal-list', '--domain', GOAL_DOMAIN, '--json')
    return any(int(g.get('id', g.get('goal_id', -1))) == int(goal_id) and
               g.get('status') in ('proposed', 'active', 'suspended') for g in rows)


def watch_set(categories, said):
    """1-2 categories to watch: [{key, label}]. The first replaces the topic."""
    out = []
    for n, c in enumerate(categories):
        args = ['--topic', WATCH_TOPIC, '--item', c['key'], '--note', c['label'],
                '--source', 'ledger-onboarding', '--json']
        if n == 0:
            args.append('--replace')
        out.append(run('preference-add', *(args + _anchor('categories', said)))['id'])
    return out


def answer_note(question, said, ref):
    """An answer kept outside the kit: a pack_file pointer + the words."""
    args = ['--domain', ANCHOR_DOMAIN, '--question', question,
            '--landed', 'pack_file:ledger/onboarding.json#' + ref, '--json']
    if said:
        args += ['--words', said]
    return run('consult-answer-add', *args)


def spend_categories(since):
    return run('spend-category-list', '--since', since, '--json')


def spend_total(since):
    return int(run('spend-category-list', '--since', since, '--sum', '--json')['total'])


# ---- onboarding v5: records profile + payline writes ------------------------
# Reads: the kit's ledger_profile verbs (every number the onboarding says).
# Writes: the existing payline verbs, find-first (method-add by name,
# fixed-add --find-first by normalized name).

def records_info():
    return run('ledger-records', '--json')


def methods_seen():
    return run('methods-seen', '--json')


def fixed_detect():
    return run('fixed-detect', '--json')


def spend_habits():
    return run('spend-habits', '--json')


def recurring_summary():
    return run('recurring-summary', '--json')


def _pref(topic, item, note, said=None, question='methods', detail=None):
    args = ['--topic', topic, '--item', item, '--note', str(note), '--source', 'ledger-onboarding', '--json']
    if detail is not None:
        args[6:6] = ['--detail', json.dumps(detail, sort_keys=True)]
    return run('preference-add', *(args + _anchor(question, said)))['id']


def method_role_set(method_id, role, said=None, inferred=False):
    """A payment method's roles (salary_fixed | living | saving | business;
    one or more -- owner rehearsal 2026-10-11): one active ledger_preference
    row per method (topic ledger_method_role, item method:<id>, note the
    keys joined by ','), revised on every answer; no pay_method column."""
    roles = [role] if isinstance(role, str) else list(role)
    return _pref('ledger_method_role', 'method:%d' % int(method_id), ','.join(roles), said,
                 detail={'from': 'inferred' if inferred else 'owner'})


def method_memo_set(method_id, memo, said=None):
    """The owner's own words on what a method is for (topic ledger_method_memo)."""
    return _pref('ledger_method_memo', 'method:%d' % int(method_id), memo, said)


def meal_voucher_set(method_id, amount, said=None):
    """Won the employer pays per workday through this method (topic
    ledger_meal_voucher; 0 = none): only the part above it is spending."""
    return _pref('ledger_meal_voucher', 'method:%d' % int(method_id), int(amount), said)


def fixed_transfer_set(fixed_id, how, inferred=True, said=None):
    """auto | manual for a fixed cost (topic ledger_fixed_transfer): read by
    payday notices later (auto = a balance notice the day before, manual = a
    reminder on the day). Stored only."""
    return _pref('ledger_fixed_transfer', 'fixed:%d' % int(fixed_id), how, said, question='fixed',
                 detail={'from': 'inferred' if inferred else 'owner'})


def fixed_purpose_set(fixed_id, purpose, said=None):
    """The group the owner put a fixed cost in (topic ledger_fixed_purpose)."""
    return _pref('ledger_fixed_purpose', 'fixed:%d' % int(fixed_id), purpose, said, question='fixed')


def review_set(topic, key, note, said=None):
    """The agent's review of the recurring summary: ledger_merchant_merge
    (note = the key it joins) | ledger_category_fix (note = category) |
    ledger_fixed_hint (note = fixed | variable), item key:<merchant key>."""
    return _pref(topic, 'key:%s' % key, note, said, question='fixed')


def flow_picture(months=3):
    return run('flow-picture', '--months', int(months), '--json')


def method_rows():
    """[{id, name, kind, linked (account name | None), aliases}] from
    method-list --json."""
    rows = pay_methods()
    names = {m['id']: m['name'] for m in rows}
    return [{'id': m['id'], 'name': m['name'], 'kind': m['kind'], 'aliases': m.get('aliases') or [],
             'linked': names.get(m.get('linked_account_id'))} for m in rows]


def method_add(name, kind, link=None):
    args = [name, kind]
    if link:
        args += ['--link', link]
    out = run('method-add', *args, parse=False)
    return int(out.splitlines()[0].split('\t', 1)[0])


def method_upd(method_id, **fields):
    return run('method-upd', int(method_id), *['%s=%s' % kv for kv in sorted(fields.items())], parse=False)


def method_link(card, account):
    return run('method-link', card, account, parse=False)


def fixed_list():
    return run('fixed-list', '--json')


def fixed_put(name, amount, cycle='monthly', next_due=None, method=None, direction='expense',
              source='interview'):
    """find-first: -> ('added' | 'exists', id). Amounts above the entry
    ceiling pass: the owner confirmed them in this conversation."""
    args = [name, int(amount), '--cycle', cycle, '--direction', direction, '--source', source,
            '--confirm', '--find-first']
    if next_due:
        args += ['--next-due', next_due]
    if method:
        args += ['--method', method]
    try:
        out = run('fixed-add', *args, parse=False)
    except KitError as e:
        if e.code == 3 and e.message.startswith('EXISTS'):
            return 'exists', int(e.message.split('\t')[1].split()[0])
        raise
    return 'added', int(out.splitlines()[0].split('\t', 1)[0])


def fixed_upd(fixed_id, **fields):
    return run('fixed-upd', int(fixed_id), *['%s=%s' % kv for kv in sorted(fields.items())], parse=False)


def fixed_correct(ref, fields, direction=None):
    args = [ref, *['%s=%s' % kv for kv in fields.items()], '--json']
    if direction is not None:
        args += ['--direction', direction]
    return run('fixed-correct', *args)


def fixed_del(fixed_id):
    return run('fixed-del', int(fixed_id), parse=False)


def fixed_alias_add(fixed_id, bank_text):
    """A renamed fixed cost keeps the bank text for matching (kit reads it
    in fixed-detect / flow-picture / fixed-add --find-first)."""
    tag = hashlib.sha1(bank_text.strip().casefold().encode('utf-8')).hexdigest()[:10]
    args = ['--topic', 'ledger_fixed_alias', '--item', 'fixed:%d/%s' % (int(fixed_id), tag),
            '--note', bank_text, '--source', 'ledger-onboarding', '--json']
    return run('preference-add', *args)['id']


def sub_tiers(month=None):
    return run('sub-tiers', *(['--month', month] if month else []), '--json')


def payday_set(fixed_id, rule, said=None):
    args = ['--topic', 'ledger_payday', '--item', 'fixed:%d' % int(fixed_id), '--note', rule,
            '--source', 'ledger-onboarding', '--json']
    return run('preference-add', *(args + _anchor('income', said)))['id']


def gap_set(source, detail, said=None):
    """Where the money the records cannot explain came from (owner review
    2026-10-10 20:56): savings | income | unknown, one active row (topic
    ledger_gap) revised on every answer; detail = amounts / noted income."""
    args = ['--topic', 'ledger_gap', '--item', 'gap:monthly', '--note', source,
            '--detail', json.dumps(detail, sort_keys=True), '--source', 'ledger-onboarding', '--json']
    return run('preference-add', *(args + _anchor('gap', said)))['id']


def goal_fields(goal_id, said=None, question='wrap', **fields):
    """goal-set with any of title / target / horizon / detail / metric / dir."""
    flag = {'title': '--title', 'target': '--target', 'horizon': '--horizon', 'detail': '--detail',
            'metric': '--metric', 'dir': '--dir'}
    args = [goal_id]
    for k, v in fields.items():
        if k == 'detail' and not isinstance(v, str):
            v = json.dumps(v, ensure_ascii=False, sort_keys=True)
        args += [flag[k], '' if v is None else v]
    return _goal_id(run('goal-set', *(args + _anchor(question, said)), parse=False))


def installment_candidates():
    try:
        return run('installment-detect', '--json')
    except KitError:
        return []
