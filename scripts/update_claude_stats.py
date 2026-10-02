"""Build aggregate Claude Code usage cards from local session logs.

Run this on the machine that owns ~/.claude/projects. No transcript text, paths,
project names, or session IDs are written to the repository.
"""
import collections
import datetime as dt
import json
import os
from pathlib import Path
import sys
from decimal import Decimal
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from update_stats import card, five_year_start

ROOT = Path(__file__).resolve().parents[1]
LOG_ROOT = Path(os.environ.get('CLAUDE_PROJECTS_DIR', Path.home() / '.claude' / 'projects'))
METRICS = ('input_tokens', 'output_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens')
# USD per million tokens: input, output, 5m write, 1h write, cache read.
# https://platform.claude.com/docs/en/about-claude/pricing (checked 2026-10-01)
RATES = {
    'claude-opus-5': ('5', '25', '6.25', '10', '0.50'),
    'claude-opus-5-5': ('4', '20', '5', '8', '0.20'),
    'claude-fable-5': ('10', '50', '12.50', '20', '1'),
    'claude-fable-5-1': ('10', '50', '12.50', '20', '0.25'),
    'claude-sonnet-5': ('2', '10', '2.50', '4', '0.20'),
    'claude-sonnet-5-5': ('2', '10', '2.50', '4', '0.20'),
    'claude-sonnet-4-6': ('3', '15', '3.75', '6', '0.30'),
    'claude-haiku-4-5-20251001': ('1', '5', '1.25', '2', '0.10'),
}


def api_equivalent_cost(model, usage, values):
    if model not in RATES:
        raise ValueError(f'No verified API rate for model {model}')
    cache = usage.get('cache_creation') or {}
    one_hour = cache.get('ephemeral_1h_input_tokens', 0)
    five_min = cache.get('ephemeral_5m_input_tokens', 0)
    if type(one_hour) is not int or type(five_min) is not int or min(one_hour, five_min) < 0:
        raise ValueError('Invalid cache write duration breakdown')
    if cache and one_hour + five_min != values['cache_creation_input_tokens']:
        raise ValueError('Cache write breakdown does not match total')
    if not cache:
        five_min = values['cache_creation_input_tokens']  # Legacy logs lack duration.
    rates = tuple(Decimal(price) for price in RATES[model])
    counts = (values['input_tokens'], values['output_tokens'], five_min,
              one_hour, values['cache_read_input_tokens'])
    return sum((Decimal(count) * rate / 1_000_000 for count, rate in zip(counts, rates)), Decimal(0))


def collect(root, start, end):
    if not root.is_dir():
        raise ValueError('Claude Code project log directory is unavailable')
    seen = set()
    totals = collections.Counter()
    models = collections.defaultdict(collections.Counter)
    sessions = set()
    monthly = collections.defaultdict(lambda: {'tokens': 0, 'cost': Decimal(0)})
    total_cost = Decimal(0)
    files = 0
    for path in root.rglob('*.jsonl'):
        files += 1
        with path.open(encoding='utf-8', errors='replace') as stream:
            for line in stream:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue  # An in-progress final line is common.
                if record.get('type') != 'assistant':
                    continue
                message = record.get('message') or {}
                usage = message.get('usage')
                model = message.get('model')
                message_id = message.get('id')
                session_id = record.get('sessionId')
                if not isinstance(usage, dict) or not isinstance(model, str) or not model.startswith('claude-'):
                    continue
                if not isinstance(message_id, str) or not isinstance(session_id, str):
                    continue
                try:
                    date = dt.datetime.fromisoformat(record['timestamp'].replace('Z', '+00:00')).astimezone(dt.timezone.utc).date()
                except (KeyError, ValueError, TypeError):
                    continue
                if not start <= date <= end:
                    continue
                key = (session_id, message_id)
                if key in seen:
                    continue
                values = {}
                for name in METRICS:
                    value = usage.get(name)
                    if type(value) is not int or value < 0:
                        raise ValueError(f'Invalid {name} in Claude Code log')
                    values[name] = value
                cost = api_equivalent_cost(model, usage, values)
                seen.add(key)
                sessions.add(session_id)
                totals.update(values)
                models[model].update(values)
                month = date.strftime('%Y-%m')
                monthly[month]['tokens'] += sum(values.values())
                monthly[month]['cost'] += cost
                total_cost += cost
    if files == 0 or not seen:
        raise ValueError('No Claude Code usage records in the selected period')
    return totals, models, len(seen), len(sessions), monthly, total_cost


def monthly_chart(monthly, start, end):
    months = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        months.append(f'{year:04d}-{month:02d}')
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    width, height = 940, 510
    left, right = 88, 900
    step = (right - left) / len(months)
    bar_width = max(1, step - 3)
    lines = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">',
             '<title>Monthly Claude Code usage · Last 5 years</title>',
             f'<desc>Recorded monthly tokens and estimated API cost, {start} to {end} UTC. Boundary months are partial; months without local records have no bars.</desc>',
             f'<rect x="1" y="1" width="{width-2}" height="{height-2}" rx="12" fill="#161b22" stroke="#30363d"/>',
             '<g font-family="Arial, sans-serif" fill="#e6edf3">',
             '<text x="24" y="34" font-size="20" font-weight="bold">Monthly Claude Code usage · Last 5 years</text>',
             f'<text x="24" y="57" font-size="12" fill="#9da7b3">{start} – {end} UTC</text>']
    for key, title, color, top in [('tokens', 'Recorded tokens', '#79c0ff', 102),
                                   ('cost', 'Estimated API equivalent · USD', '#d2a8ff', 302)]:
        values = [float(monthly[m][key]) if m in monthly else 0 for m in months]
        maximum = max(values, default=0) or 1
        baseline = top + 140
        lines.append(f'<text x="24" y="{top-15}" font-size="14" fill="{color}">{title}</text>')
        for fraction in (0, 0.5, 1):
            y = baseline - 140 * fraction
            value = maximum * fraction
            label = f'{value/1_000_000_000:,.1f}B' if key == 'tokens' and maximum >= 1_000_000_000 else (f'{value/1_000_000:,.1f}M' if key == 'tokens' else f'${value:,.0f}')
            lines.append(f'<line x1="{left}" x2="{right}" y1="{y}" y2="{y}" stroke="#30363d"/>')
            lines.append(f'<text x="{left-8}" y="{y+4}" text-anchor="end" font-size="11" fill="#9da7b3">{label}</text>')
        for index, (month_label, value) in enumerate(zip(months, values)):
            x = left + index * step + 1.5
            bar_height = 140 * value / maximum
            tooltip = f'{month_label}: {int(value):,} tokens' if key == 'tokens' else f'{month_label}: ${value:,.2f} estimated API cost'
            lines.append(f'<rect x="{x:.2f}" y="{baseline-bar_height:.2f}" width="{bar_width:.2f}" height="{bar_height:.2f}" fill="{color}" data-month="{month_label}" data-series="{key}"><title>{escape(tooltip)}</title></rect>')
            if index in (0, len(months)-1) or month_label.endswith('-01'):
                label = month_label if index in (0, len(months)-1) else month_label[:4]
                lines.append(f'<text x="{x+bar_width/2:.2f}" y="{baseline+19}" text-anchor="middle" font-size="10" fill="#9da7b3">{label}</text>')
    lines.append(f'<text x="24" y="490" font-size="11" fill="#9da7b3">Updated {end} UTC</text>')
    lines.append('</g></svg>')
    svg = '\n'.join(lines) + '\n'
    ET.fromstring(svg)
    return svg


def render(totals, models, calls, sessions, monthly, total_cost, start, end):
    total_tokens = sum(totals.values())
    rows = [
        ('Total processed tokens', total_tokens),
        ('API equivalent cost (USD)', f'${total_cost:,.2f}'),
        ('Input tokens', totals['input_tokens']),
        ('Output tokens', totals['output_tokens']),
        ('Cache creation tokens', totals['cache_creation_input_tokens']),
        ('Cache read tokens', totals['cache_read_input_tokens']),
        ('Model responses', calls),
        ('Sessions', sessions),
    ]
    model_rows = sorted(((name, sum(counts.values())) for name, counts in models.items()),
                        key=lambda item: (-item[1], item[0]))
    if len(model_rows) > 8:
        model_rows = model_rows[:8] + [('Other Claude models', sum(value for _, value in model_rows[8:]))]
    day = end.isoformat()
    note = f'{start} to {end} UTC · Local Claude Code logs'
    return {
        'claude-usage.svg': card('Claude Code · Last 5 years', rows, note, day),
        'claude-models.svg': card('Tokens by Claude model', model_rows,
                                  'Input + output + cache creation + cache read', day),
        'claude-monthly.svg': monthly_chart(monthly, start, end),
    }


def main():
    end = dt.datetime.now(dt.timezone.utc).date()
    start = five_year_start(end)
    totals, models, calls, sessions, monthly, total_cost = collect(LOG_ROOT, start, end)
    outputs = render(totals, models, calls, sessions, monthly, total_cost, start, end)
    for name, svg in outputs.items():
        ET.fromstring(svg)
        target = ROOT / 'profile' / name
        temporary = target.with_suffix('.svg.tmp')
        temporary.write_text(svg, encoding='utf-8')
        temporary.replace(target)
    print(f'Claude Code usage refreshed: {calls} responses, {sessions} sessions, {len(models)} models, ${total_cost:,.2f} API equivalent', file=sys.stderr)


if __name__ == '__main__':
    main()
