from datetime import datetime, timedelta
import json
from pathlib import Path
import sys
import time

import matplotlib
matplotlib.use('Agg')
import matplotlib.dates as mdates
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
DAY = sys.argv[1] if len(sys.argv) > 1 else time.strftime('%Y-%m-%d')
SRC = [ROOT / f'data/usage/{DAY}.jsonl']
START = datetime.strptime(DAY, '%Y-%m-%d')
OUT = ROOT / f'data/usage/chart-{DAY}-{time.strftime("%H%M")}.png'

rows = []
for f in SRC:
    rows += [json.loads(line) for line in f.read_text(encoding='utf-8').splitlines() if line.strip()]
rows.sort(key=lambda r: r['t'])
rows = [r for r in rows if datetime.fromtimestamp(r['t']) >= START]

hours = sorted({time.strftime('%Y-%m-%d %H', time.localtime(r['t'])) for r in rows})
agg = {h: {'cached': 0, 'uncached': 0, 'completion': 0} for h in hours}
for r in rows:
    h = time.strftime('%Y-%m-%d %H', time.localtime(r['t']))
    prompt = max(0, int(r.get('prompt') or 0))
    cached_tok = min(max(0, int(r.get('cached') or 0)), prompt)
    agg[h]['cached'] += cached_tok
    agg[h]['uncached'] += prompt - cached_tok
    agg[h]['completion'] += max(0, int(r.get('completion') or 0))

cached = [agg[h]['cached'] / 1e6 for h in hours]
uncached = [agg[h]['uncached'] / 1e6 for h in hours]
completion = [agg[h]['completion'] / 1e6 for h in hours]

fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14.3, 9.9), dpi=100)
x = list(range(len(hours)))

ax1.bar(x, cached, color='#7ba0bb', label='cached prompt', edgecolor='white', linewidth=0.4)
ax1.bar(x, uncached, bottom=cached, color='#d98b84', label='uncached prompt', edgecolor='white', linewidth=0.4)
ax1.bar(x, completion, bottom=[c + u for c, u in zip(cached, uncached)],
        color='#e0c25e', label='completion (M/10)', edgecolor='white', linewidth=0.4)
ax1.set_xticks(x)
ax1.set_xticklabels([datetime.strptime(h, '%Y-%m-%d %H').strftime('%d %H:00') for h in hours], rotation=0)
ax1.set_ylabel('tokens (M)')
ax1.set_title('token usage per hour (cached / uncached / output)')
ax1.grid(True, alpha=0.3)
ax1.legend(loc='upper right', framealpha=0.9)
ax1.set_axisbelow(True)

times = [datetime.fromtimestamp(r['t']) for r in rows]
prompts = [max(0, int(r.get('prompt') or 0)) / 1e3 for r in rows]
ax2.plot(times, prompts, color='#a4747f', linewidth=1.1)
ax2.set_ylabel('prompt tokens (x1k)')
ax2.set_title('prompt size per call (jagged = tidy / compress)')
ax2.grid(True, alpha=0.3)
ax2.xaxis.set_major_formatter(mdates.DateFormatter('%d %H:%M'))
ax2.xaxis.set_major_locator(mdates.MinuteLocator(byminute=[0, 30]))
if times:
    ax2.set_xlim(START, times[-1] + timedelta(minutes=10))
for label in ax2.get_xticklabels():
    label.set_rotation(30)
    label.set_horizontalalignment('right')

if rows:
    biggest = max(rows, key=lambda r: max(0, int(r.get('prompt') or 0)))
    ax2.annotate('%dk' % (max(0, int(biggest.get('prompt') or 0)) / 1e3),
                 xy=(datetime.fromtimestamp(biggest['t']), max(0, int(biggest.get('prompt') or 0)) / 1e3),
                 xytext=(6, 4), textcoords='offset points', fontsize=8, color='#555')

fig.tight_layout()
fig.savefig(OUT)
print('saved', OUT)

for h in hours:
    a = agg[h]
    print(f"{h}  calls={sum(1 for r in rows if time.strftime('%Y-%m-%d %H', time.localtime(r['t'])) == h):4d}"
          f"  cached={a['cached']/1e6:7.2f}M  uncached={a['uncached']/1e6:6.2f}M  out={a['completion']/1e3:6.1f}k")
