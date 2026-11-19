// The unit may precede a number ("百分点差为 11.62") or follow it.
// Keep this typed: an arithmetic difference cannot vouch for a count or a rate.
export function isPercentagePointValue(text, match) {
  const before=text.slice(Math.max(0,match.index-30),match.index);
  const after=text.slice(match.index+match.raw.length,match.index+match.raw.length+12);
  return /^\s*(?:个)?百分点/.test(after)||
    /百分点(?:差(?:额)?|差异)?\s*(?:为|是|约为|约|等于|：|:)?\s*$/.test(before);
}

export function percentagePointValues(text) {
  const plain=String(text).replaceAll('**','');
  return [...plain.matchAll(/(?<![\d.])(-?\d+(?:\.\d+)?)/g)]
    .filter(m=>isPercentagePointValue(plain,{index:m.index,raw:m[1]})).map(m=>Number(m[1]));
}
