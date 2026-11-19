const methods = new Map([
  ['naive', {name: '末值基线', explanation: '用最后一期实际值估计目标期'}],
  ['linear', {name: '线性趋势', explanation: '拟合历史数据的整体变化趋势，再沿趋势推算目标期'}],
  ['ets', {name: '季节平滑', explanation: '从历史数据估计整体走势和季节规律，再结合两者推算目标期'}],
  ['arima', {name: '自回归时间序列', explanation: '根据各期历史值的前后关联推算目标期'}],
  ['snaive', {name: '季节朴素', explanation: '用上一季节周期同月的实际值估计目标期'}],
  ['smean', {name: '历史同期均值', explanation: '用历年同月实际值的平均估计目标期'}],
  ['theta', {name: 'Theta 趋势', explanation: '分离季节影响后拟合整体趋势，再沿趋势推算目标期'}],
  ['holt', {name: 'Holt 趋势', explanation: '平滑历史水平与走势后沿趋势推算目标期，不假设季节规律'}],
]);

export const forecastMethodPresentation = model => methods.get(model);

const twoDecimals = new Intl.NumberFormat('zh-CN', {maximumFractionDigits:2});
const smallAmount = new Intl.NumberFormat('zh-CN', {maximumFractionDigits:8});
export const isCurrencyForecastUnit = unit => /^(?:人民币)?(?:万|亿)?元$/.test(unit||'');
export const forecastAmountLabel = value => (Math.abs(value)>=.01?twoDecimals:smallAmount).format(value);
export const forecastErrorLabel = value => value>0&&value<.01 ? '接近0' : twoDecimals.format(value);

const bases = new Map([
  ['random_walk_normal_approximation', '随机游走残差正态近似（不含参数不确定性）'],
  ['linear_student_t_prediction', 'Student-t 预测区间（含参数与杠杆不确定性）'],
  ['seasonal_naive_normal_approximation', '季节差分残差正态近似'],
  ['seasonal_mean_student_t_prediction', '同季节均值 Student-t 预测区间'],
  ['holt_bootstrap_prediction', '残差 bootstrap 重拟合+模拟池（含参数与创新不确定性）'],
  ['ets_bootstrap_prediction', '残差 bootstrap 重拟合+模拟池（含参数与创新不确定性）'],
  ['theta_model_prediction', 'Theta 模型预测区间'],
  ['arima_model_prediction', 'ARIMA 模型置信区间'],
]);

export const forecastBasisPresentation = basis => bases.get(basis);

const exclusionReasons = new Map([
  ['insufficient_history_for_same_model_backtest', '同模型回测历史不足'],
  ['fit_failed', '拟合失败'],
  ['dependency_unavailable', '运行依赖缺失'],
  ['infeasible_count_interval', '计数区间不可行'],
]);

export const forecastExclusionPresentation = reason => exclusionReasons.get(reason);
