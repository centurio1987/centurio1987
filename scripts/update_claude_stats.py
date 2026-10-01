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

from update_stats import card

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
                    date = dt.datetime.fromisoformat(record['timestamp'].replace('Z', '+00:00')).date()
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
    width, height = 760, 95 + 33 * len(months)
    maximum = max((monthly[m]['tokens'] for m in months if m in monthly), default=0)
    lines = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">',
             '<title>Claude Code monthly token usage and API equivalent cost</title>',
             f'<desc>{start} to {end} UTC. Boundary months are partial. Bar length shows tokens; USD amount is estimated API equivalent cost.</desc>',
             f'<rect x="1" y="1" width="{width-2}" height="{height-2}" rx="12" fill="#161b22" stroke="#30363d"/>',
             '<g font-family="Arial, sans-serif" fill="#e6edf3">',
             '<text x="24" y="34" font-size="19" font-weight="bold">Monthly Claude Code usage</text>',
             '<text x="482" y="58" font-size="11" fill="#9da7b3">TOKENS</text>',
             '<text x="661" y="58" font-size="11" fill="#9da7b3">API USD</text>']
    for index, label in enumerate(months):
        y = 80 + 33 * index
        tokens = monthly[label]['tokens'] if label in monthly else 0
        cost = monthly[label]['cost'] if label in monthly else Decimal(0)
        bar_width = round(330 * tokens / maximum) if maximum else 0
        lines.append(f'<text x="24" y="{y+14}" font-size="13">{escape(label)}</text>')
        lines.append(f'<rect x="112" y="{y}" width="330" height="18" rx="4" fill="#30363d"/>')
        if bar_width:
            lines.append(f'<rect x="112" y="{y}" width="{bar_width}" height="18" rx="4" fill="#79c0ff"/>')
        lines.append(f'<text x="600" y="{y+14}" text-anchor="end" font-size="13">{tokens/1_000_000:,.1f}M</text>')
        lines.append(f'<text x="736" y="{y+14}" text-anchor="end" font-size="13">${cost:,.2f}</text>')
    lines.append(f'<text x="24" y="{height-13}" font-size="11" fill="#9da7b3">{start} to {end} UTC · first/last month may be partial</text>')
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
        'claude-usage.svg': card('Claude Code · Last 365 days', rows, note, day),
        'claude-models.svg': card('Tokens by Claude model', model_rows,
                                  'Input + output + cache creation + cache read', day),
        'claude-monthly.svg': monthly_chart(monthly, start, end),
    }


def main():
    end = dt.datetime.now(dt.timezone.utc).date()
    start = end - dt.timedelta(days=364)
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
