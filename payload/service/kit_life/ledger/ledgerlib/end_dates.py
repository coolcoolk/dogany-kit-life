"""Owner-approved end-date copy (candidate A, approved loan question)."""
import os
from . import text


def enabled():
    return os.environ.get('LEDGER_END_DATES_UI', '1') != '0'


def t(key, **kw):
    return text.t('ledger.end_dates.'+key, **kw)


def loan_guess(loan):
    if loan.get('repayment_type_source') == 'owner':
        return ''
    kind = loan.get('repayment_guess', 'unknown')
    if kind == 'equal_payment':
        return t('guess_equal_payment', amount=format(loan['latest_payment'], ','))
    if kind in ('equal_principal', 'bullet', 'credit_line'):
        # Owner pick 2026-10-31: candidate B (latest-amount frame).
        return text.t('ledger.loan_guess.'+kind+'.b', amount=format(loan['latest_payment'], ','))
    return ''


def label(name, end):
    if not enabled() or not end:
        return name
    year, month = map(int,end[:7].split('-'))
    return t('label',name=name,year=year,month=month)
