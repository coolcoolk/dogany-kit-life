"""An ask is an executable continuation, carrying its inputs and offered values.

No shell evaluation: the relay dispatches argument lists through existing kit
verbs, and preserves every refusal/exit status. New questions are not success.
"""
import base64
import json
import shlex

CURRENT_ARGV = []

# DEFER_ASK: a tapped label or typed word maps to the machine key. "\uc62e\uae30\uae30"
# / "Move" are the legacy move button and must keep working on old messages.
DEFER_ALIASES = {
    "\uc62e\uae30\uae30": "move", "Move": "move", "\ubcc0\uacbd": "move", "Change": "move",
    "\uadf8\ub300\ub85c \ub450\uae30": "keep", "Keep as is": "keep",
}


def context(ref, choices, argv=None, **extra):
    data = dict(ref=str(ref), choices=[str(k) for k,_ in choices],
                argv=list(CURRENT_ARGV if argv is None else argv), **extra)
    return base64.urlsafe_b64encode(json.dumps(data,ensure_ascii=False).encode()).decode()


def command(token, ref, choices, **extra):
    direct = {'ORIGIN_ASK':'appt-origin', 'CLASH_ASK':'clash-answer'}
    if token in direct:
        return ('kit_life.sh %s %s {value}' % (direct[token], shlex.quote(str(ref))) + (' --ask ' + extra['ask'] if token=='CLASH_ASK' and extra.get('ask') else ''))
    return 'kit_life.sh ask-answer %s %s {value}' % (token, context(ref,choices,**extra))


def cli(K, args):
    if len(args)!=3:
        K._err('usage: kit_life.sh ask-answer <token> <context> <value>')
    token,encoded,value=args
    try:
        data=json.loads(base64.urlsafe_b64decode(encoded).decode())
    except (ValueError, UnicodeError):
        K._err('invalid ask context')
    ref,argv=data['ref'],data['argv']
    if token=='DEFER_ASK':
        # labels of the current and the pre-2026-10-27 keyboard ("\uc62e\uae30\uae30")
        value=DEFER_ALIASES.get(value.strip(),value)
    if data['choices'] and value not in data['choices'] and token != 'MEAL_TIME_ASK':
        K._err('answer was not offered')
    def run(*a):
        return K.main(list(a))
    def replay(extra):
        if not argv:
            K._err('missing original action; ask again through the original kit verb')
        return K.main(argv+extra)
    if token=='PLACE_ASK':
        return run('appt-upd',ref,'location='+value)
    if token=='PREP_ASK':
        return run('config-set','travel_prep_min='+value)
    if token=='MAP_PROVIDER_ASK':
        return run('map-provider-set',ref,value)
    if token=='SATIETY_ASK':
        date,slot=ref.split('_',1)
        return run('meal-satiety',date,slot,value,*(['--occasion',data['occasion']] if data.get('occasion') else []))
    if token=='MEAL_TIME_ASK':
        return run('meal-time',ref,value)
    if token=='MEAL_CONFIRM_ASK':
        return run('meal-confirm',ref,value)
    if token=='SPEND_CATEGORY_ASK':
        return run('shop-cat-upd',ref,'--spend-category',value)
    if token=='TYPE_ASK':
        return run('noun-type',ref,value,*(['--parent',str(data['parent'])] if value=='sub_place' and data.get('parent') else []))
    if token=='ROOM_ASK':
        return replay(['room='+value]) if ref=='-' else run('appt-upd',ref,'room='+value)
    if token=='PARENT_ASK':
        eid,name=ref.split(':',1)
        if value=='place:other':
            return followup(K,token,data,K._i18n('noun.parent.other','another place'))
        run('noun-type',name,'sub_place','--parent',value)
        if eid!='-':
            return run('appt-upd',eid,'room='+name)
        return
    if token=='AMBIGUOUS_KIND_ASK':
        if value=='none':
            return run('noun-type',ref,'not_a_name')
        kind,_=value.split(':',1)
        return run('noun-type',ref,kind,'--alias-of',value)
    if token=='RETIRE_ASK':
        if value=='retire':
            return run('routine','retire',ref)
        print('ASK_ANSWERED\t%s\t%s\t%s'%(token,ref,value));return
    if token=='END_ASK':
        if value=='pick':
            return followup(K,token,data,K._i18n('routine.end_ask.date','Which date?'))
        extra=['open=1'] if value=='open' else ['until='+ref] if value=='until' else ['end='+value]
        return replay(extra)
    if token=='DEFER_ASK':
        if value=='keep':
            print('ASK_ANSWERED\t%s\t%s\t%s'%(token,ref,value));return
        return replay(['--apply'])
    if token=='DEL_ASK':
        if value=='keep':
            print('ASK_ANSWERED\t%s\t%s\t%s'%(token,ref,value));return
        count=data.get('expected_sets')
        if count is None:
            K._err('missing deletion snapshot; ask again')
        return replay(['--confirm-destroy-sets',str(count)])
    if token=='ERRAND_ASK':
        if value=='booked':
            return replay(['--booked'])
        if data.get('task_argv'):
            return K.main(data['task_argv'])
        K._err('errand answer needs the original appointment fields')
    if token=='WEEKLY_CATCHUP_ASK':
        import weekly_catchup
        weekly_catchup.set_value(K._kit_root(),value)
        print('ASK_ANSWERED\t%s\t%s\t%s'%(token,ref,value));return
    if token=='PLANNED_SWAP_ASK':
        planned,eaten=ref.split(':',1)
        return run('meal-planned-swap',planned,*(['--eaten',eaten] if value=='yes' else ['--keep']))
    if token=='PLANNED_MEAL_ASK':
        return run('meal-planned-answer',ref,value)
    if token=='TARGET_ASK':
        return run('meal-target-set',*shlex.split(value))
    if token=='STOCK_ASK':
        return run('shop-stock-set',ref,*shlex.split(value))
    if token=='SHOP_PAYMENT_ASK':
        return run('shop-payment-match',ref,'--captures',value)
    K._err('unsupported ask token '+token)


def followup(K,token,data,question):
    print('%s\t%s\t%s'%(token,data['ref'],question))
    print(K._ask_choices().options_line(token,data['ref'],[],argv=data['argv']))
