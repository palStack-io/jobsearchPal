"""Stats contract v2: what a pal answers on GET /api/admin/stats for adminPal's Home.

Totals only, counted on the pal's own server. Fields a pal cannot compute stay None
(adminPal shows them as unknown); adminPal withholds any count under 10 itself.
"""

_COUNT_FIELDS = ('people', 'active_30d', 'new_30d', 'paying', 'paying_start_30d', 'cancelled_30d',
                 'upgraded_30d', 'payment_failed_30d', 'revenue_month_cents')


def _whole(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def stats_v2(*, people, active_30d, new_30d, paying=None, paying_start_30d=None, cancelled_30d=None,
             upgraded_30d=None, payment_failed_30d=None, revenue_month_cents=None, revenue_estimated=True,
             weekly=None, cards=None) -> dict:
    values = dict(people=people, active_30d=active_30d, new_30d=new_30d, paying=paying,
                  paying_start_30d=paying_start_30d, cancelled_30d=cancelled_30d, upgraded_30d=upgraded_30d,
                  payment_failed_30d=payment_failed_30d, revenue_month_cents=revenue_month_cents)
    out = {'version': 2, **{k: _whole(values[k]) for k in _COUNT_FIELDS},
           'revenue_estimated': bool(revenue_estimated)}
    out['weekly'] = [{'week_start': str(w.get('week_start', ''))[:10], 'people': _whole(w.get('people')),
                      'active': _whole(w.get('active'))}
                     for w in (weekly or [])[:60] if isinstance(w, dict)]
    out['cards'] = [{'label': str(c.get('label', ''))[:40], 'value': c.get('value'), 'accent': c.get('accent')}
                    for c in (cards or []) if isinstance(c, dict)][:8]
    return out
