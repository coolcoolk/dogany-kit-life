"""Typed future lifecycle facts; payment history remains actual and unchanged."""
import datetime


def matching(row, meta):
    for key, fact in meta.items():
        if not key.startswith('lifecycle:'):
            continue
        if fact.get('id') == row['id'] or (
                fact.get('scope') == 'series' and (
                    fact.get('stream_id') and fact['stream_id'] == row.get('stream_id') or
                    not fact.get('stream_id') and (fact['name'], fact['method']) == (row['name'], row['method']))):
            yield fact


def future_excluded(row, meta, today):
    return any(f.get('one_off') or f.get('renewal_expected') or
               f.get('stopped_on') and f['stopped_on'] <= today for f in matching(row, meta))


def save(c, data, today):
    from ledger_plan import facts, put, _advance_date
    from ledger_diag import rows
    if type(data.get('id')) is not int:
        raise ValueError('payment lifecycle needs an exact payment id')
    hits = rows(c, "SELECT * FROM spend_entry WHERE id=? AND kind='expense' AND scope<>'business'", (data['id'],))
    if not hits:
        raise ValueError('existing personal payment required')
    row = hits[0]
    prior = facts(c).get('lifecycle:'+str(row['id']), {})
    fact = dict(prior, id=row['id'], name=row['name'], method=row['method'], stream_id=row['stream_id'],
                said=data['said'], provenance='owner')
    if not any(k in data for k in ('one_off', 'stopped_on', 'remaining_paid_months', 'paid_through', 'renewal_expected')):
        raise ValueError('typed one-off, stop or paid-term fact required')
    if 'one_off' in data:
        if type(data['one_off']) is not bool:
            raise ValueError('one_off requires boolean')
        fact['one_off'] = data['one_off']
    if 'stopped_on' in data:
        fact['stopped_on'] = datetime.date.fromisoformat(data['stopped_on']).isoformat() if data['stopped_on'] else None
    if 'remaining_paid_months' in data:
        count = data['remaining_paid_months']
        if type(count) is not int or not 0 <= count <= 1200:
            raise ValueError('remaining paid months requires a nonnegative integer')
        anchor = datetime.date.fromisoformat(data.get('as_of', today)).isoformat()
        if anchor > today:
            raise ValueError('paid-term anchor cannot be in the future')
        renewal = _advance_date(anchor, count)
        fact.update(remaining_paid_months=count, as_of=anchor,
                    paid_through=(datetime.date.fromisoformat(renewal)-datetime.timedelta(days=1)).isoformat(),
                    renews_on=renewal)
    for field in ('paid_through', 'renews_on'):
        if field in data:
            fact[field] = datetime.date.fromisoformat(data[field]).isoformat() if data[field] else None
    if 'renewal_expected' in data:
        if type(data['renewal_expected']) is not bool:
            raise ValueError('renewal_expected requires boolean')
        fact['renewal_expected'] = data['renewal_expected']
    if 'renewal_amount' in data and data['renewal_amount'] is not None and (
            type(data['renewal_amount']) is not int or data['renewal_amount'] < 0):
        raise ValueError('renewal amount requires nonnegative whole won or null')
    if fact.get('renewal_expected'):
        fact['renewal_amount'] = data.get('renewal_amount', prior.get('renewal_amount'))
    if fact.get('one_off') and (fact.get('renewal_expected') or fact.get('stopped_on')):
        raise ValueError('one-off and a continuing service lifecycle conflict')
    scope = data.get('scope', prior.get('scope', 'item' if fact.get('one_off') else 'series'))
    if scope not in ('item', 'series'):
        raise ValueError('lifecycle scope requires item or series')
    fact['scope'] = scope
    put(c, 'lifecycle:'+str(row['id']), fact)
    # A dated stop reaches every consumer of an existing stream. The dated
    # fact also handles unlinked series and future-effective stops on reads.
    if scope == 'series' and row['stream_id'] and fact.get('stopped_on') and fact['stopped_on'] <= today:
        c.execute("UPDATE fixed_cost SET state='stopped',stopped_by='owner',stopped_at=? WHERE id=?",
                  (fact['stopped_on'], row['stream_id']))
    return dict(saved=True, kind='payment_lifecycle', fact=fact)
