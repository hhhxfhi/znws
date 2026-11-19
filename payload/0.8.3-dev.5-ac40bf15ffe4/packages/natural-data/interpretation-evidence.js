// A snapshot rate describes a state. Calling progress slow/fast additionally
// needs a schedule, target or comparator that was actually evaluated.
export function progressInterpretationIssues(text,evidence,userText='') {
  const benchmark=evidence.some(e=>(e.claims||[]).some(c=>c.operation==='schedule_compare'&&c.verified===true))||
    /(?:进度计划|进度基准|计划(?:完成率|进度)|(?:同期|同月|本期)进度目标)/.test(userText)&&
    evidence.some(e=>(e.claims||[]).some(c=>c.operation==='branch_compare'&&typeof c.matched==='boolean'));
  if(benchmark)return [];
  const unsupported=String(text).replaceAll('**','').split(/[。；;\n]/).some(sentence=>{
    const claim=sentence.match(/(?:进度|进展|验收环节|办理节奏)[^，,。；;\n]{0,12}(?:偏慢|较慢|缓慢|滞后|偏快|较快|超前)|(?:整体|总体)[^，,。；;\n]{0,8}(?:偏慢|缓慢|滞后)/);
    if(!claim)return false;
    const before=sentence.slice(0,claim.index).split(/[，,]/).at(-1)||'';
    return !/(?:无法|不能|不可|尚未|不宜|不代表|不等于|没有依据|不足以)[^，,]{0,16}$/.test(before)&&
      !/^(?:与否|的说法)?(?:尚无依据|不能判断|无法判断|仍待核实)/.test(sentence.slice(claim.index+claim[0].length));
  });
  return unsupported?['进度快慢没有已核对的计划、目标或时间对照；单期百分比只能描述当前状态。保留已查询的比例和分母，说明尚不能评价快慢，不要求用户选择技术算法。']:[];
}
