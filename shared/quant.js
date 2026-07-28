// Crypto/DeFi/trading quant formulas.
//
// Extracted from an uploaded reference file (satoshi_crypto.py) and cleaned
// up: the knowledge-graph/"consciousness"/fake-learning wrapper around these
// formulas was dropped (it didn't compute anything real), keeping only the
// actual math. Not wired into agents/trading-agent.js yet — available for
// future strategy iterations (e.g. Kelly-sized positions, RSI/Bollinger
// confirmation filters).

// --- Technical indicators ---------------------------------------------------

function rsi(prices, period = 14) {
  if (prices.length < period + 1) return 50;
  let gainSum = 0;
  let lossSum = 0;
  for (let i = 1; i <= period; i++) {
    const change = prices[prices.length - i] - prices[prices.length - i - 1];
    if (change > 0) gainSum += change;
    else lossSum += Math.abs(change);
  }
  const avgGain = gainSum / period;
  const avgLoss = lossSum / period;
  if (avgLoss === 0) return 100;
  const rs = avgGain / avgLoss;
  return 100 - 100 / (1 + rs);
}

// Returns { upper, mid, lower }.
function bollingerBands(prices, period = 20, stdDev = 2.0) {
  if (prices.length < period) return { upper: 0, mid: 0, lower: 0 };
  const window = prices.slice(-period);
  const mean = window.reduce((a, b) => a + b, 0) / period;
  const variance = window.reduce((a, p) => a + (p - mean) ** 2, 0) / period;
  const std = Math.sqrt(variance);
  return { upper: mean + stdDev * std, mid: mean, lower: mean - stdDev * std };
}

// --- Portfolio / risk metrics ------------------------------------------------

function sharpeRatio(returns, riskFree = 0) {
  if (returns.length < 2) return 0;
  const mean = returns.reduce((a, b) => a + b, 0) / returns.length;
  const variance = returns.reduce((a, r) => a + (r - mean) ** 2, 0) / (returns.length - 1);
  const std = Math.sqrt(variance);
  return std > 0 ? (mean - riskFree) / std : 0;
}

function maxDrawdown(prices) {
  let maxDd = 0;
  let peak = prices[0];
  for (const p of prices) {
    peak = Math.max(peak, p);
    maxDd = Math.max(maxDd, (peak - p) / peak);
  }
  return maxDd;
}

function volatility(returns, periodsPerYear = 365) {
  if (returns.length < 2) return 0;
  const mean = returns.reduce((a, b) => a + b, 0) / returns.length;
  const variance = returns.reduce((a, r) => a + (r - mean) ** 2, 0) / (returns.length - 1);
  return Math.sqrt(variance) * Math.sqrt(periodsPerYear);
}

// Fraction of bankroll to risk. winLossRatio = avg win size / avg loss size.
function kellyCriterion(winProb, winLossRatio) {
  const b = winLossRatio;
  const p = winProb;
  const q = 1 - p;
  return b > 0 ? (b * p - q) / b : 0;
}

function positionSize(portfolioValue, riskPerTrade, stopLossPct) {
  return stopLossPct > 0 ? (portfolioValue * riskPerTrade) / stopLossPct : 0;
}

// --- Derivatives -------------------------------------------------------------

function fundingRatePremium(indexPrice, markPrice) {
  return indexPrice !== 0 ? (markPrice - indexPrice) / indexPrice : 0;
}

function deltaNeutralHedge(spotPosition, perpPosition) {
  return spotPosition - perpPosition;
}

// --- AMM / DEX ----------------------------------------------------------------

function ammPrice(xReserve, yReserve) {
  return xReserve > 0 ? yReserve / xReserve : 0;
}

function ammSwapOut(dx, xReserve, yReserve, fee = 0.003) {
  const dxEff = dx * (1 - fee);
  return (yReserve * dxEff) / (xReserve + dxEff);
}

function impermanentLoss(p0, p1) {
  const ratio = p1 / p0;
  return (2 * Math.sqrt(ratio)) / (1 + ratio) - 1;
}

function slippage(dx, xReserve) {
  return dx / (xReserve + dx);
}

function priceImpact(dx, xReserve, yReserve, fee = 0.003) {
  const dy = ammSwapOut(dx, xReserve, yReserve, fee);
  const spot = yReserve / xReserve;
  const execPrice = dy / dx;
  return spot !== 0 ? (execPrice - spot) / spot : 0;
}

// --- Lending / liquidation ----------------------------------------------------

function lendingHealthFactor(collateral, collateralPrice, ltv, debt) {
  return debt > 0 ? (collateral * collateralPrice * ltv) / debt : Infinity;
}

function liquidationPrice(collateral, debt, threshold = 0.825) {
  return collateral > 0 ? debt / (collateral * threshold) : Infinity;
}

function flashLoanProfit(arbitrageSpread, loanAmount, fee = 0.0009) {
  return arbitrageSpread * loanAmount - loanAmount * fee;
}

// --- Staking / yield ------------------------------------------------------------

function stakingApy(rewardPerBlock, blocksPerYear, totalStaked, priceReward, priceStaked) {
  const annualRewards = rewardPerBlock * blocksPerYear * priceReward;
  const stakedValue = totalStaked * priceStaked;
  return stakedValue > 0 ? annualRewards / stakedValue : 0;
}

function compoundApy(apr, compoundsPerYear = 365) {
  return (1 + apr / compoundsPerYear) ** compoundsPerYear - 1;
}

// --- Tokenomics -----------------------------------------------------------------

function marketCap(price, circulatingSupply) {
  return price * circulatingSupply;
}

function fdv(price, maxSupply) {
  return price * maxSupply;
}

function tvlRatio(marketCapUsd, tvl) {
  return tvl > 0 ? marketCapUsd / tvl : 0;
}

function nvtRatio(marketCapUsd, txVolume24h) {
  return txVolume24h > 0 ? marketCapUsd / txVolume24h : 0;
}

function mvrvRatio(marketCapUsd, realizedCap) {
  return realizedCap > 0 ? marketCapUsd / realizedCap : 0;
}

module.exports = {
  rsi,
  bollingerBands,
  sharpeRatio,
  maxDrawdown,
  volatility,
  kellyCriterion,
  positionSize,
  fundingRatePremium,
  deltaNeutralHedge,
  ammPrice,
  ammSwapOut,
  impermanentLoss,
  slippage,
  priceImpact,
  lendingHealthFactor,
  liquidationPrice,
  flashLoanProfit,
  stakingApy,
  compoundApy,
  marketCap,
  fdv,
  tvlRatio,
  nvtRatio,
  mvrvRatio,
};
