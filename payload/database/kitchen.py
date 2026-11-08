"""Cooking view of owned gear and facts that decorate those possessions."""
import json
from pathlib import Path
import re

VERBS = ('kitchen-list', 'kitchen-fact-add', 'kitchen-memory-backfill')

def gear(c):
    rows = c.execute("SELECT o.id,i.name,i.brand,i.variant,cat.name,o.bought_on FROM owned_item o JOIN shop_item i ON i.id=o.item_id JOIN shop_category cat ON cat.id=i.category_id WHERE o.status='in_use' AND cat.consumable=0 ORDER BY o.bought_on,o.id").fetchall()
    out=[]
    for oid,name,brand,variant,category,bought_on in rows:
        facts={}
        for key,raw in c.execute("SELECT name,detail FROM ledger_resource WHERE kind='kitchen' AND status='active' AND owned_item_id=? ORDER BY id",(oid,)):
            try: value=json.loads(raw) if raw else True
            except ValueError: value=raw
            facts[key]=value
        appliance=category=='gadget' or re.search(r'oven|air.?fryer|\uc624\ube10|\uc5d0\uc5b4\ud504\ub77c\uc774\uc5b4|\uc804\uc790\ub808\uc778\uc9c0',name,re.I)
        out.append(dict(owned_id=oid,kind='appliance' if appliance else 'cookware',name=name,brand=brand,model=variant,bought_on=bought_on,facts=facts))
    for rid,name,raw in c.execute("SELECT id,name,detail FROM ledger_resource WHERE kind='kitchen' AND status='active' AND owned_item_id IS NULL ORDER BY id"):
        try: detail=json.loads(raw) if raw else {}
        except ValueError: detail={'note':raw}
        out.append(dict(resource_id=rid,kind='fixture',name=name,facts=detail))
    return out

def fact_add(K,c,owned_id,key,value,source='chat',commit=True):
    owned_id=int(owned_id)
    row=c.execute("SELECT i.name FROM owned_item o JOIN shop_item i ON i.id=o.item_id WHERE o.id=? AND o.status='in_use'",(owned_id,)).fetchone()
    if not row: raise ValueError('active owned item not found')
    now,raw=K._utc_now_str(),json.dumps(value,ensure_ascii=False)
    old=c.execute("SELECT id FROM ledger_resource WHERE kind='kitchen' AND name=? AND owned_item_id=? AND status='active'",(key,owned_id)).fetchone()
    if old:
        c.execute("UPDATE ledger_resource SET detail=?,as_of=?,source=?,recorded_at=? WHERE id=?",(raw,now[:10],source,now,old[0])); action='updated'
    else:
        c.execute("INSERT INTO ledger_resource(ulid,kind,name,detail,as_of,status,source,recorded_at,owned_item_id) VALUES(?,'kitchen',?,?,?,'active',?,?,?)",(K.new_ulid(),key,raw,now[:10],source,now,owned_id)); action='added'
    if commit:
        c.commit()
    return dict(action=action,owned_id=owned_id,item=row[0],key=key,value=value)

def tune_plan(c,plan):
    ovens=[g for g in gear(c) if g['kind']=='appliance' and re.search(r'oven|air.?fryer|\uc624\ube10|\uc5d0\uc5b4\ud504\ub77c\uc774\uc5b4',g['name'],re.I)]
    hot=next((g for g in ovens if g['facts'].get('runs_hot')),None)
    if not hot: return plan
    delta=int(hot['facts'].get('temperature_offset_c',-10))
    for step in plan['steps']:
        if step['heat']!='oven': continue
        if 'temperature_c' in step:
            step['standard_temperature_c']=step['temperature_c']; step['temperature_c']+=delta
        for field in ('action','warning'):
            step[field]=re.sub(r'(\d{2,3})\s*\u00b0?C',lambda m:'%d\u00b0C'%(int(m.group(1))+delta),step.get(field,''))
        step['gear_adjustment']='runs_hot:%+dC'%delta
    return plan

def backfill(K,c,owned_id,memory_path):
    """Import agent-reviewed typed facts; a memory sentence is not a command."""
    facts = json.loads(Path(memory_path).read_text(encoding='utf-8'))
    if not isinstance(facts, dict):
        raise ValueError('kitchen backfill requires a JSON fact object')
    return [fact_add(K,c,owned_id,k,v,'memory_backfill') for k,v in facts.items()]

def cli(K,verb,argv):
    c=K.get_conn()
    try:
        if verb=='kitchen-list':
            if argv: raise ValueError('usage: kitchen-list')
            print('\n'.join('KITCHEN\t'+json.dumps(x,ensure_ascii=False,sort_keys=True) for x in gear(c)) or 'NONE\tkitchen')
        elif verb=='kitchen-fact-add':
            if len(argv)!=2 or '=' not in argv[1]: raise ValueError('usage: kitchen-fact-add OWNED_ID key=JSON_VALUE')
            key,raw=argv[1].split('=',1)
            try: value=json.loads(raw)
            except ValueError: value=raw
            print('KITCHEN_WRITTEN\t'+json.dumps(fact_add(K,c,argv[0],key,value),ensure_ascii=False))
        else:
            if len(argv)!=3 or argv[1]!='--memory': raise ValueError('usage: kitchen-memory-backfill OWNED_ID --memory FILE')
            print('BACKFILLED\t%d'%len(backfill(K,c,argv[0],argv[2])))
    finally: c.close()


def appliance(c):
    """Read the existing possession and kitchen fact stores before asking."""
    for item in reversed(gear(c)):
        if not re.search(r'oven|\uc624\ube10', item['name'], re.I):
            continue
        facts = item['facts']
        result = dict(type='oven', model=facts.get('model') or item.get('model'))
        if item.get('owned_id'):
            result['owned_id'] = item['owned_id']
        modes = facts.get('modes', [])
        if isinstance(modes, str):
            modes = [modes]
        modes = [m for m in modes if m in ('top_bottom', 'convection')]
        if 'modes' not in facts:
            if facts.get('top_bottom') is True:
                modes.append('top_bottom')
            if facts.get('convection') is True:
                modes.append('convection')
        if modes:
            result['modes'] = sorted(set(modes))
        return result
    return {}
