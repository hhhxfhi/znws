"""Evidence-backed wording for quantitative claims; no generated arithmetic."""
import hashlib
import json

def num(v):
    return '不可计算' if v is None else f'{v:,.2f}'.rstrip('0').rstrip('.')

def label(f):
    return '、'.join(str(v) for v in f.get('dimensions',{}).values()) or '汇总'

def render(claim,contract):
    if claim.get('operation')=='field_statistics':return claim['computed_text']
    if claim.get('operation')=='matrix_method':return claim['computed_text']
    unit=contract.get('unit','')
    if claim.get('operation')=='detail_threshold_summary':
        return f"当前结果共{claim['count']}条明细，其中{claim['matched_count']}条满足 {claim['comparison']} {num(claim['threshold'])}{unit}；数值范围{num(claim['minimum'])}{unit}至{num(claim['maximum'])}{unit}。仅核验当前返回结果，不推断结果外记录。"
    if claim.get('operation')=='sum':
        scope='；'.join(('统计期间' if k=='period' else k)+'：'+ '、'.join(v) for k,v in claim['dimensions'].items())
        if claim.get('sum_basis')=='category_occurrences':
            return f"{claim['selection']}分类计次合计{num(claim['value'])}项次；包含{claim['count']}条统计结果。同一实体可在不同分类重复计次，不是跨分类去重实体数。范围：{scope}。"
        return f"{claim['selection']}合计{num(claim['value'])}{unit}；包含{claim['count']}条统计结果。范围：{scope}。"
    if 'current' in claim and 'baseline' in claim:
        a,b=claim['current'],claim['baseline']
        delta=claim.get('difference',a['value']-b['value'])
        bd=b.get('dimensions',{});ad=a.get('dimensions',{})
        same_group={k:v for k,v in bd.items() if k!='period'}=={k:v for k,v in ad.items() if k!='period'}
        temporal=same_group and bd.get('period')!=ad.get('period') and bd.get('period') is not None and ad.get('period') is not None
        direction=('增加' if delta>0 else '减少' if delta<0 else '持平') if temporal else ('高于对照值' if delta>0 else '低于对照值' if delta<0 else '与对照值相等，差额')
        change=claim.get('change_percent')
        text=f"{label(b)}：{num(b['value'])}{unit}；{label(a)}：{num(a['value'])}{unit}。"
        text+=f"{direction}{num(abs(delta))}{claim.get('difference_unit',claim.get('unit',unit))}。"
        if 'change_percent' in claim:
            change_label='变化率' if temporal else '相对对照值的差异比例'
            text+=f"{change_label}{num(change)}{'%' if change is not None else '（缺相邻基期或基期为零）'}。"
        if claim.get('anomaly_threshold') is not None:
            text+=f"跌幅严格超过{num(abs(claim['anomaly_threshold']))}%对应变化率 < -{num(abs(claim['anomaly_threshold']))}%，恰好相等不触发；本组{'触发' if claim.get('anomaly') else '未触发' if claim.get('anomaly') is False else '不可判定'}。"
        if not temporal:
            if b['value']:
                text+=f"本值为对照值的{num(a['value']/b['value'])}倍（{num(100*a['value']/b['value'])}%）；差异比例以差额除以对照值，二者不能混用。"
            text+='这是不同对象的数值对照，不表示随时间增长或下降。'
        if 'matched' in claim:text+=f"差额{'满足' if claim['matched'] else '不满足'} {claim['comparison']} {num(claim['threshold'])}{claim['difference_unit']}。"
        if claim.get('operation')=='aggregate_compare':
            text+='这是所选全部月份分别合计后的变化率，不是各月变化率相加。'
            if 'change_matched' in claim:
                text+=f"合计变化率 {claim['change_comparison']} {num(claim['change_threshold'])}%："+('无法判定（基期为零）。' if claim['change_matched'] is None else '已触发。' if claim['change_matched'] else '未触发。')
            # Keep the peak evidence in the claim, but don't narrate a branch
            # highlight in the result card when its threshold was not met.
            for c in claim.get('major_changes',[]) if claim.get('change_matched') is not False else []:
                text+=f"其中{label(c['current'])}{'增量' if c['difference']>0 else '减量'}最大，为{num(abs(c['difference']))}{unit}。"
        return text
    if 'fact' in claim:
        f=claim['fact'];text=f"{label(f)}：{num(f['value'])}{unit if f['value'] is not None else ''}。"
        if 'matched' in claim:text+=f"{'满足' if claim['matched'] else '不满足'} {claim['comparison']} {num(claim['threshold'])}{unit}。"
        if 'matched' in claim and 'difference' in claim:
            delta=claim['difference'];du='个百分点' if unit=='%' else unit
            text+=f"{'高于阈值' if delta>0 else '低于阈值' if delta<0 else '与阈值相等，差额'}{num(abs(delta))}{du}。"
        if 'share_percent' in claim:text+=f"占比{num(claim['share_percent'])}%；分母{num(claim['denominator'])}{unit}。"
        if 'cumulative_percent' in claim:
            total_unit='项次' if contract.get('overlapping_dimensions') else unit
            if 'cumulative_value' in claim:text+=f"累计前{claim['prefix_count']}项合计{num(claim['cumulative_value'])}{total_unit}；"
            text+=f"累计占比{num(claim['cumulative_percent'])}%。"
            if contract.get('overlapping_dimensions'):text+='此处为分类计次，不是跨分类去重实体数。'
        return text
    if claim.get('operation')=='trend_summary':
        text='、'.join(str(v) for v in claim.get('dimensions',{}).values())+f"有效相邻比较{claim['comparisons']}组：增加{claim['increases']}组，减少{claim['decreases']}组，持平{claim['unchanged']}组；缺基期或零基期{claim['unknown']}组。有效变化率范围{num(claim['minimum_change'])}%至{num(claim['maximum_change'])}%。"
        if claim.get('increase_minimum') is not None:
            text+=f"增长比较的变化率范围{num(claim['increase_minimum'])}%至{num(claim['increase_maximum'])}%，不能用局部区间代替全部增长区间。"
        for key,title in [('minimum_facts','最低'),('maximum_facts','最高')]:
            facts=claim.get(key,[])
            if facts:text+=f"本次观测{title}："+'；'.join(f"{label(f)}为{num(f['value'])}{unit}" for f in facts)+'。'
        return text
    if claim.get('skill_id')=='distribution_profile':
        return f"当前同口径分组{claim['count']}项，最小值{num(claim['minimum'])}{unit}，最大值{num(claim['maximum'])}{unit}；分组算术平均{num(claim['mean'])}{unit}。"
    if claim.get('skill_id')=='economic_efficiency':
        return '资金执行率描述支出相对于预算的进度，不代表投入产出效益或验收进度。'
    if claim.get('skill_id')=='anomaly_attribution':
        return f"IQR识别出{len(claim['outliers'])}个统计异常观测；没有原因证据，未完成因果归因。"
    return ''

def finalize(claims,contract):
    output=[]
    for claim in claims:
        digest=hashlib.sha256(json.dumps(claim,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:20]
        refs=list(claim.get('included_fact_ids',[]))
        for key in ['fact','baseline','current']:
            if isinstance(claim.get(key),dict) and claim[key].get('id'):refs.extend(claim[key].get('derived_from',[claim[key]['id']]))
        output.append({**claim,'claim_id':'claim:'+digest,'fact_refs':refs,'metric_id':contract['id'],
                       'scope':contract.get('scope',{}),'verified_text':render(claim,contract),'evidence_level':'descriptive'})
    return output

def model_claims(claims):
    """Forecast history stays in the dataset and verified result view."""
    output=[]
    for claim in claims:
        stats=claim.get('statistics')
        if (claim.get('operation')=='matrix_method' and isinstance(stats,dict)
                and stats.get('kind')=='forecast' and 'history' in stats):
            claim={**claim,'statistics':{k:v for k,v in stats.items() if k!='history'}}
        output.append(claim)
    return output

def trend_summary(claims):
    valid=[x for x in claims if x.get('change_percent') is not None]
    values=[x['change_percent'] for x in valid]
    facts={f['id']:f for claim in claims for key in ['baseline','current'] if (f:=claim.get(key)) and f.get('id')}
    observations=list(facts.values())
    minimum=min((f['value'] for f in observations),default=None)
    maximum=max((f['value'] for f in observations),default=None)
    increases=[v for v in values if v>0]
    return {'operation':'trend_summary','comparisons':len(valid),'increases':sum(x>0 for x in values),
            'included_fact_ids':list(facts),
            'minimum_facts':[f for f in observations if f['value']==minimum],
            'maximum_facts':[f for f in observations if f['value']==maximum],
            'increase_minimum':min(increases) if increases else None,
            'increase_maximum':max(increases) if increases else None,
            'decreases':sum(x<0 for x in values),'unchanged':sum(x==0 for x in values),
            'unknown':len(claims)-len(valid),'minimum_change':min(values) if values else None,
            'maximum_change':max(values) if values else None}
