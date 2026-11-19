"""Dialect-specific building blocks, not industry-specific query recipes."""

_TEXT_SAMPLE_SQL = ('SELECT 字段 FROM (SELECT DISTINCT 字段 FROM 表 WHERE 字段 IS NOT NULL) AS d '
                    'ORDER BY LENGTH(字段) DESC LIMIT 20')
_LONG_TEXT_SAMPLE = ('用 purpose=explore 核对较长的不同原值和实际分隔符。普通 LIMIT 的前几行不能代表全部格式。'
                     '保留与探查相容的已有筛选，在去重的子查询内添加筛选。')


def distinct_text_sample(dialect):
    if dialect in ('sqlite', 'postgres', 'postgresql', 'tbase', 'kingbase', 'mysql'):
        return _TEXT_SAMPLE_SQL
    return None


def guidance(dialect):
    common = (_LONG_TEXT_SAMPLE + '再按分组拆开，分离组标签和每个项目。'
              '同一组中的第二个、第三个项目仍属于该组，不能只匹配字符串开头，也不能跨组匹配。'
              '项目或分组没有标签前缀时原样保留，不得因缺少分隔符而丢弃或截断；剥离前缀仅在确认存在标签分隔符时进行。'
              '名单/计数按用户指定对象去重，不能用证书数替代单位数。'
              '分组结果用一行一个分组的长表，保留标签；合计和前N占比从同一份最终结果计算，不能复用探索样本或旧查询的合计。')
    hints = {'text_expansion':common}
    if sample := distinct_text_sample(dialect):
        hints['distinct_text_sample'] = sample
    if dialect == 'sqlite':
        return {**hints,
            'example':"已筛选的 base 含字段 attrs。分组：CROSS JOIN json_each('[' || replace(json_quote(b.attrs), '；', char(34)||','||char(34)) || ']') g；组标签：CASE WHEN instr(g.value,'：')>0 THEN trim(substr(g.value,1,instr(g.value,'：')-1)) ELSE '' END（无前缀段不截断）；项目展开：CROSS JOIN json_each('[' || replace(json_quote(substr(g.value,instr(g.value,'：')+1)), '、', char(34)||','||char(34)) || ']') p（instr 为 0 时 substr 保留整段，无前缀项目不丢失）；项目值 trim(p.value)。b 是 base 的别名。组分隔符、标签分隔符、项目分隔符须换成实际观察值；保留原日期、地区和注销等条件。json_each 输出列是 value，不是 v。char(34)生成双引号，可避免JSON与SQL双重转义错误。分组标签直接作为等级或类别，不再按其他字段混合生成数字/文本类型的CASE。"}
    if dialect in ('postgres', 'postgresql', 'tbase', 'kingbase'):
        return {**hints,
            'example':"已筛选的 base 含字段 attrs：FROM base b CROSS JOIN LATERAL regexp_split_to_table(b.attrs,'；') g(segment) CROSS JOIN LATERAL regexp_split_to_table(regexp_replace(g.segment,'^[^：]*：',''),'、') p(item)。组标签 CASE WHEN g.segment LIKE '%：%' THEN trim(split_part(g.segment,'：',1)) ELSE '' END，项目值 trim(p.item)。regexp_replace 仅在存在前缀时剥离，无前缀段原样保留；不要用 split_part(segment,'：',2) 取项目（无前缀段返回空串会被丢弃）。替换真实字段与观察到的分隔符，并保留原筛选条件。"}
    return hints
