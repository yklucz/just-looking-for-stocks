"""Render saved Stage 5 results; never recompute probabilities or select a policy."""
import argparse
import json
from pathlib import Path

import pandas as pd


def table(headers, rows):
    return '\n| '+' | '.join(headers)+' |\n| '+' | '.join(['---']*len(headers))+' |\n'+''.join('| '+' | '.join(map(str, row))+' |\n' for row in rows)+'\n'


def fmt(value, percent=False):
    if value is None:
        return 'undefined'
    return f'{value*100:.2f}%' if percent else f'{value:.3f}'


def plot_results(primary: list[dict], path: Path) -> bool:
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        from matplotlib.ticker import PercentFormatter
    except ImportError:
        return False
    fig, axes = plt.subplots(2, 2, figsize=(13, 8), layout='constrained')
    for row, asset in enumerate(('AAPL', 'SPY')):
        exp = next(r for r in primary if r['source']==asset+'_expanding' and r['model']=='xgboost')
        roll = next(r for r in primary if r['source']==asset+'_rolling' and r['model']=='xgboost')
        prior = next(r for r in primary if r['source']==asset+'_expanding' and r['model']=='training_prior')
        for label, root, filename, color in (
                ('XGBoost expanding', exp['artifact_path'], 'equity.csv', '#2458a6'),
                ('XGBoost rolling', roll['artifact_path'], 'equity.csv', '#bd4c3d'),
                ('Training prior', prior['artifact_path'], 'equity.csv', '#8c6a14'),
                ('Buy & Hold', exp['artifact_path'], 'buy_hold_equity.csv', '#23704d')):
            frame = pd.read_csv(Path(root)/filename)
            dates = pd.to_datetime(frame.timestamp, utc=True)
            axes[row, 0].plot(dates, frame.equity/100000, label=label, color=color, linewidth=1.2)
            axes[row, 1].plot(dates, frame.drawdown, color=color, linewidth=1.0)
        axes[row, 0].axhline(1, color='gray', linestyle=':', linewidth=.8)
        axes[row, 0].set_yscale('log')
        axes[row, 0].set_title(asset+' — wealth multiple (log scale)')
        axes[row, 1].set_title(asset+' — drawdown at Open marks')
        axes[row, 1].yaxis.set_major_formatter(PercentFormatter(1))
        for ax in axes[row]:
            ax.grid(alpha=.18)
            ax.spines[['top', 'right']].set_visible(False)
    axes[0, 0].legend(fontsize=8)
    fig.suptitle('Frozen OOF LONG/FLAT: 0.5 cutoff, five-bar hold\nPrimary costs: 1 bp commission + 1 bp slippage per side')
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--comparison', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path('docs/stage5-research.md'))
    args = parser.parse_args()
    results = json.loads(args.comparison.read_text())['runs']
    primary = [r for r in results if r['scenario']=='primary']
    xgb = [r for r in primary if r['model']=='xgboost']
    args.output.parent.mkdir(parents=True, exist_ok=True)
    chart = args.output.with_name('stage5-equity-drawdown.png')
    plotted = plot_results(primary, chart)
    lines = ['# Stage 5 research report',
             'All results below use frozen Stage 4 probabilities. No model was fitted, no probability was changed, and no threshold was selected from backtest results. The primary cutoff remains .5 and holding period five sessions.',
             '**XGBoost made money before and after modest execution costs, but it underperformed matched Buy & Hold and the training-prior policy on both return and Sharpe in all four asset/mode runs. Positive long exposure in a rising market is not evidence of economic value from the learned signal.**',
             '## Fixed protocol',
             '64 predeclared runs: AAPL/SPY × expanding/rolling × XGBoost/logistic/momentum/training-prior × four cost assumptions. This is a complete sensitivity comparison, not a search for a winning strategy. Starting capital 100,000; size 100%; fractional adjusted units; no leverage, shorts, cash interest, or threshold sweep.',
             'Event: log(Close[t+5]/Close[t]) > .002. LONG if probability >= .5, otherwise FLAT. Entry Open[t+1], exit Open[t+6]. Signals during an active position are ignored; the exit-day close may generate the next entry for the following open. Open-to-open holding intervals do not overlap.',
             'All strategies and benchmarks use 2016-12-21 through 2025-12-10 (2,254 session intervals). Buy & Hold buys and liquidates at those endpoints, with the same costs. Cash earns zero. All required exits were supported; zero incomplete LONG trades were excluded in these actual runs.',
             'The saved daily timestamps are provider candle labels, not 05:00 UTC execution times. Execution means the actual exchange open of that labeled session. The full snapshot is used to preserve actual trading-day adjacency through OOF gaps.',
             '## Primary results',
             table(['Source','Model','Gross return','Net return','CAGR','Sharpe','Sortino','Max drawdown','Exposure','Trades'],
                   [[r['source'],r['model'],fmt(r['gross']['total_return'],True),fmt(r['net']['total_return'],True),fmt(r['net']['cagr'],True),fmt(r['net']['sharpe']),fmt(r['net']['sortino']),fmt(r['net']['max_drawdown'],True),fmt(r['net']['exposure'],True),r['net']['trade_count']] for r in primary]),
             'Gross means a separate zero-cost replay. Primary net costs are 1 bp commission plus 1 bp adverse slippage per side; this is a modest reference assumption, not an empirically measured fill model.']
    if plotted:
        lines.append(f'![Primary-cost wealth and drawdown]({chart.name})')
    lines += ['## Matched Buy & Hold and cash',
              table(['Source','XGB return','Buy & Hold return','Excess return (pp)','XGB Sharpe','B&H Sharpe','XGB drawdown','B&H drawdown'],
                    [[r['source'],fmt(r['net']['total_return'],True),fmt(r['benchmarks']['buy_hold']['total_return'],True),f"{r['excess_return_vs_buy_hold']*100:.2f}",fmt(r['net']['sharpe']),fmt(r['benchmarks']['buy_hold']['sharpe']),fmt(r['net']['max_drawdown'],True),fmt(r['benchmarks']['buy_hold']['max_drawdown'],True)] for r in xgb]),
              'Cash return and drawdown are zero. Its Sharpe and Sortino are undefined, rather than fabricated zero risk-adjusted performance. AAPL expanding XGBoost reduced drawdown, but still did not improve Sharpe/Sortino over Buy & Hold. SPY rolling slightly worsened maximum drawdown.',
              '## Cost sensitivity — every predeclared scenario',
              'Costs below are per side: zero=(0 commission, 0 slippage); primary=(1,1); higher-slippage=(1,5); stress=(5,10), all in basis points. No scenario is selected as the expected result.',
              table(['Source','Model','Zero return','Primary return','Higher-slippage return','Stress return'],
                    [[r['source'],r['model'],*[fmt(next(v for v in results if v['source']==r['source'] and v['model']==r['model'] and v['scenario']==s)['net']['total_return'],True) for s in ('zero','primary','higher_slippage','stress')]] for r in primary]),
              '## Costs, trade frequency and accounting',
              table(['Source','Model','Win rate','Profit factor','Avg win ($)','Avg loss ($)','Expectancy ($)','Entries/year','Bars between entries','Turnover × initial capital','Costs ($)','Approx break-even bps/side'],
                    [[r['source'],r['model'],fmt(r['net']['win_rate'],True),fmt(r['net']['profit_factor']),fmt(r['net']['average_win']),fmt(r['net']['average_loss']),fmt(r['net']['expectancy']),fmt(r['net']['trades_per_year']),fmt(r['net']['average_bars_between_entries']),fmt(r['net']['turnover']),fmt(r['net']['total_costs']),fmt(r['estimated_cost_break_even_bps_per_side'])] for r in primary]),
              'Every strategy trade holds five bar intervals. Turnover sums raw entry and exit notionals divided by initial capital, not average current equity; it is cumulative over almost nine years. Positive/negative trade PnL, win/loss rates, profit factor and expectancy are after costs. Quantity sizing includes entry commission inside the cash allocation.',
              'The separate gross replay compounds different quantities. Its final wealth minus net wealth therefore includes lost compounding and is not identical to cash commissions plus slippage. Within the net ledger, gross PnL uses actual quantities and reconciles exactly with net PnL plus costs.',
              'Estimated cost break-even is only a first-order dollar-PnL/two-sided-notional approximation on zero-cost quantities. It ignores cost-dependent sizing/compounding and asks when profit approaches zero, not when the strategy beats Buy & Hold. It must not be treated as a precise tolerable cost or tuned parameter.',
              '## Risk statistics',
              table(['Source','Model','Annual volatility','Sharpe','Sortino','Calmar','Max drawdown','Capital exposure'],
                    [[r['source'],r['model'],fmt(r['net']['annualized_volatility'],True),fmt(r['net']['sharpe']),fmt(r['net']['sortino']),fmt(r['net']['calmar']),fmt(r['net']['max_drawdown'],True),fmt(r['net']['average_capital_exposure'],True)] for r in primary]),
              'Equity marks are after orders at each Open. The first return interval starts from pre-entry capital so initial slippage/fees are included. Drawdown peaks include initial capital. Sharpe uses session returns, sample standard deviation, sqrt(252), and zero risk-free rate. Sortino uses RMS negative session returns across all sessions (minimum acceptable return zero). CAGR uses elapsed calendar days / 365.25. Intraday and close-only drawdown extremes are not represented.',
              '## Probability buckets — outcomes, not trade returns',
              'All 2,250 OOF observations per source are included, including those ignored by the non-overlap policy. Fixed buckets are left-inclusive, except that the last bucket also includes 1. No calibration was fitted. Adjacent five-bar target outcomes are dependent; these counts are not independent trials.']
    for r in primary:
        if r['model'] not in ('xgboost','logistic'):
            continue
        frame = pd.read_csv(Path(r['artifact_path'])/'probability_buckets.csv')
        lines += [f"### {r['source']} {r['model']}",
                  table(['Probability bucket','Count','Mean predicted probability','Actual event frequency','Mean future log return','Mean future simple return'],
                        [[f"[{v.lower:.2f}, {v.upper:.2f}{']' if v.upper_inclusive else ')'}",int(v.observations),*[fmt(None if pd.isna(x) else x,True) for x in (v.mean_probability,v.event_frequency,v.mean_future_log_return,v.mean_future_simple_return)]] for v in frame.itertuples()])]
    lines += ['Higher probabilities did not consistently correspond to better outcomes. AAPL expanding XGBoost event rates were 57.7%, 53.1%, and 50.0% in the .50–.55, .55–.60 and .60+ buckets. The last had only 18 observations. AAPL rolling improved in .55–.60 but fell back in .60+. SPY expanding .60+ averaged predicted probability 67.2%, yet event frequency was 51.1% and mean future simple return was negative. This is not monotonic evidence of useful probability ordering.',
              '## Interpretation',
              'Expanding XGBoost performed better economically than rolling on both assets here, despite slightly weaker Stage 4 AUC summaries. This is descriptive: no mode is selected or retuned after these results. Four-mode profits and often smaller drawdowns do not establish incremental ML value, because Buy & Hold and the simpler training-prior policy earned more with higher Sharpe.',
              'The training-prior probability stayed above .5 throughout these folds, so its strategy was simply a five-sessions-invested, one-session-flat schedule. It carried no observation-specific prediction advantage. Its stronger results make it especially important not to attribute XGBoost gains to learned economic edge.',
              'Logistic produced small positive primary returns but weak Sharpe and severe benchmark underperformance. Several logistic variants became negative under higher costs. Smaller exposure alone is not a demonstrated forecasting benefit. XGBoost also had substantial cost drag, and SPY rolling became negative under the stress scenario.',
              '## Source integrity and limitations',
              'The loader checks original manifests, fingerprints, required files, OOF/market/target timestamps, ticker/interval, and fold availability. Stage 5 outputs link original manifest hashes and configuration fingerprints. Execution receives probabilities and Open prices; future targets are isolated in diagnostic reporting. No Stage 4 files or model states are overwritten.',
              'Consistent adjusted OHLC is inferred from the inspected Yahoo retrieval path and yfinance auto_adjust=True default. Stage 4 lacks explicit adjustment flags and the yfinance version, so this convention is recorded as inferred rather than vendor-attested. Adjusted fractional units avoid mixing raw Open with adjusted Close; dividends are not credited again. This remains a research return proxy, not a historical broker dividend, tax or share-count ledger.',
              'No market impact, open-auction liquidity constraints, integer-share rounding, tax, cash yield, stops, borrowing, or broker integration is modeled. Finite positive snapshot prices do not guarantee real fills. The small two-asset historical sample, overlapping target outcomes and retrospective data adjustments limit inference. Backtest performance does not guarantee future performance.',
              '## Artifact index',
              f"Comparison manifest: [{args.comparison.name}](../{args.comparison.as_posix()}). Each primary row below links its human-readable report; all 64 runs and all output paths appear in the comparison manifest.",
              table(['Source','Model','Primary report'],[[r['source'],r['model'],f"[{Path(r['artifact_path']).name}](../{Path(r['artifact_path']).relative_to(Path.cwd())}/report.md)"] for r in primary]),
              'Each run saves configuration, machine-readable summary, trades, equity, separate gross replay, matching Buy & Hold/cash curves, probability buckets, and a checksum manifest. Raw execution prices in trades mean the frozen adjusted snapshot values before simulated slippage, not historically unadjusted exchange prices.',
              '## Next stage',
              'Stage 5 is complete as an execution/accounting framework. There is no robust evidence of incremental economic value from the current ML signal. Stage 6 may introduce a GRU only as another research candidate to evaluate against unchanged simple baselines under the same temporal and execution framework. No PyTorch implementation has begun.']
    args.output.write_text('\n\n'.join(lines)+'\n')
    print(args.output.resolve())


if __name__ == '__main__':
    main()
