"""每小时上下文成本图：按 llm_system/config.json 里的真实单价把 token 加权成人民币。

价格不再写死：直接复用 `mods/llm/pricing.py` 和 `data/storage/llm_system/config.json` 的
`price_fn`（峰谷档位和法定节假日表都在里面），每一行按它自己的模型算钱。账本里出现没配价的
模型时不计钱、只计入"未计价"计数，并在末尾报出来。

用法：python scripts/cost_chart.py [YYYY-MM-DD]
"""
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import sys
import time

import matplotlib
matplotlib.use('Agg')
import matplotlib.dates as mdates
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'data/storage/llm_system/config.json'
PRICING = ROOT / 'mods/llm/pricing.py'
_PRICE_KEYS = ('price_fn', 'prompt_price', 'prompt_cached_price', 'completion_price', 'input_price', 'output_price')


def _load_pricing():
    """按文件路径加载 pricing.py，避免把整个 mods.llm 包（openai 客户端等）拉起来。"""
    spec = importlib.util.spec_from_file_location('dsh_pricing', PRICING)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DAY = sys.argv[1] if len(sys.argv) > 1 else time.strftime('%Y-%m-%d')
SRC = ROOT / f'data/usage/{DAY}.jsonl'
OUT = ROOT / f'data/usage/cost-{DAY}-{time.strftime("%H%M")}.png'

pricing = _load_pricing()
config = json.loads(CONFIG.read_text(encoding='utf-8'))
providers = config.get('providers') or {}

rows = [json.loads(line) for line in SRC.read_text(encoding='utf-8').splitlines() if line.strip()]
rows.sort(key=lambda r: r['t'])
start = datetime.strptime(DAY, '%Y-%m-%d')

_cache = {}


def _has_pricing(block: dict, info: dict) -> bool:
    for source in (info, block):
        if any(source.get(key) not in (None, '') for key in _PRICE_KEYS):
            return True
    return False


def _prices(model, moment):
    """一次调用的三项单价；模型没配价时返回 None。按小时缓存，price_fn 每小时内不变。"""
    key = (model, moment.strftime('%Y-%m-%d %H'))
    if key in _cache:
        return _cache[key]
    found = None
    if '/' in (model or ''):
        provider, name = model.split('/', 1)
        block = providers.get(provider) or {}
        info = (block.get('models') or {}).get(name)
        if isinstance(info, dict) and _has_pricing(block, info):
            candidate = pricing.unit_prices(block, info, moment)
            if any(value > 0 for value in candidate.values()):
                found = candidate
    _cache[key] = found
    return found


hours = sorted({time.strftime('%Y-%m-%d %H', time.localtime(r['t'])) for r in rows})
agg = {h: {'hit': 0.0, 'miss': 0.0, 'out': 0.0} for h in hours}
tok = {h: {'prompt': 0, 'out': 0} for h in hours}
models = {}
unpriced = {}
for r in rows:
    when = datetime.fromtimestamp(r['t'], timezone.utc)
    h = datetime.fromtimestamp(r['t']).strftime('%Y-%m-%d %H')
    models[r['model']] = models.get(r['model'], 0) + 1
    unit = _prices(r['model'], when)
    prompt = max(0, int(r.get('prompt') or 0))
    cached = min(max(0, int(r.get('cached') or 0)), prompt)
    completion = max(0, int(r.get('completion') or 0))
    tok[h]['prompt'] += prompt
    tok[h]['out'] += completion
    if unit is None:
        unpriced[r['model']] = unpriced.get(r['model'], 0) + 1
        continue
    miss = prompt - cached
    agg[h]['hit'] += cached * unit['prompt_cached_price'] / 1e6
    agg[h]['miss'] += miss * unit['prompt_price'] / 1e6
    agg[h]['out'] += completion * unit['completion_price'] / 1e6

hit = [agg[h]['hit'] for h in hours]
miss = [agg[h]['miss'] for h in hours]
out = [agg[h]['out'] for h in hours]
total = [a + b + c for a, b, c in zip(hit, miss, out)]

fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14.3, 9.9), dpi=100)
x = list(range(len(hours)))

ax1.bar(x, hit, color='#7ba0bb', label='cached input', edgecolor='white', linewidth=0.4)
ax1.bar(x, miss, bottom=hit, color='#d98b84', label='uncached input', edgecolor='white', linewidth=0.4)
ax1.bar(x, out, bottom=[a + b for a, b in zip(hit, miss)], color='#e0c25e',
        label='output', edgecolor='white', linewidth=0.4)
for i, v in enumerate(total):
    ax1.text(i, v, '¥%.2f' % v, ha='center', va='bottom', fontsize=9)
ax1.set_xticks(x)
ax1.set_xticklabels([datetime.strptime(h, '%Y-%m-%d %H').strftime('%H:00') for h in hours])
ax1.set_ylabel('cost (CNY)')
ax1.set_ylim(0, max(total) * 1.15 if total else 1)
ax1.set_title('%s cost per hour — priced from llm_system/config.json' % DAY)
ax1.grid(True, alpha=0.3)
ax1.legend(loc='upper right', framealpha=0.9, fontsize=8)
ax1.set_axisbelow(True)

times = [datetime.fromtimestamp(r['t']) for r in rows]
ax2.plot(times, [max(0, int(r.get('prompt') or 0)) / 1e3 for r in rows], color='#a4747f', linewidth=1.1)
ax2.set_ylabel('prompt tokens (x1k)')
ax2.set_title('prompt size per call (jagged = tidy / compress)')
ax2.grid(True, alpha=0.3)
ax2.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
ax2.xaxis.set_major_locator(mdates.MinuteLocator(byminute=[0, 30]))
if times:
    ax2.set_xlim(start, times[-1] + timedelta(minutes=10))
for label in ax2.get_xticklabels():
    label.set_rotation(30)
    label.set_horizontalalignment('right')

fig.tight_layout()
fig.savefig(OUT)
print('saved', OUT)
print('模型分布:', ', '.join('%s×%d' % (m, n) for m, n in sorted(models.items(), key=lambda kv: -kv[1])))
if unpriced:
    print('未计价（config 里没配价）:', ', '.join('%s×%d' % (m, n) for m, n in unpriced.items()))
print('日期 %s 共 %d 次调用' % (DAY, len(rows)))
print('%-10s %6s %10s %10s %10s %9s' % ('小时', '调用', '¥缓存', '¥未命中', '¥输出', '合计'))
for h in hours:
    a = agg[h]
    t = a['hit'] + a['miss'] + a['out']
    print('%-10s %6d %10.3f %10.3f %10.3f %9.3f' % (
        h[-2:] + ':00', sum(1 for r in rows if time.strftime('%Y-%m-%d %H', time.localtime(r['t'])) == h),
        a['hit'], a['miss'], a['out'], t))
print('合计 ¥%.2f（缓存 %.2f + 未命中 %.2f + 输出 %.2f）' % (
    sum(total), sum(hit), sum(miss), sum(out)))
print('tokens: prompt %s, output %s' % (f'{sum(t["prompt"] for t in tok.values()):,}',
                                        f'{sum(t["out"] for t in tok.values()):,}'))
