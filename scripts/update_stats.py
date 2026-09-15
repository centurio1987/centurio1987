"""Render validated local SVG cards using public repository metadata only."""
import collections
import datetime
import json
import os
from pathlib import Path
import re
import subprocess
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]


def summarize(pages):
    repos = {}
    for page in pages:
        if not isinstance(page, list):
            raise ValueError('Expected a repository page')
        for repo in page:
            if repo['private'] is False and repo['fork'] is False:
                repos[repo['id']] = repo
    values = list(repos.values())
    stats = [('Public repositories', len(values)),
             ('Stars received', sum(r['stargazers_count'] for r in values)),
             ('Forks received', sum(r['forks_count'] for r in values))]
    languages = sorted(collections.Counter(r['language'] for r in values if r['language']).items(),
                       key=lambda item: (-item[1], item[0]))[:8]
    return stats, languages


def card(title, rows, note, day):
    height = 114 + 30 * max(1, len(rows))
    lines = [f'<svg xmlns="http://www.w3.org/2000/svg" width="480" height="{height}" viewBox="0 0 480 {height}" role="img">',
             f'<title>{escape(title)}</title><desc>{escape(note)}. Updated {day} UTC.</desc>',
             f'<rect x="1" y="1" width="478" height="{height-2}" rx="12" fill="#161b22" stroke="#30363d"/>',
             '<g font-family="Arial, sans-serif" fill="#e6edf3">',
             f'<text x="24" y="34" font-size="19" font-weight="bold">{escape(title)}</text>']
    for i, (label, value) in enumerate(rows or [('No language metadata', 0)]):
        y = 67 + i * 30
        lines.append(f'<text x="24" y="{y}" font-size="14">{escape(label)}</text>')
        lines.append(f'<text x="450" y="{y}" text-anchor="end" font-size="16" fill="#79c0ff">{value:,}</text>')
    lines.extend([f'<text x="24" y="{height-35}" font-size="11" fill="#9da7b3">{escape(note)}</text>',
                  f'<text x="24" y="{height-17}" font-size="11" fill="#9da7b3">Updated {day} UTC</text>', '</g></svg>'])
    svg = '\n'.join(lines) + '\n'
    ET.fromstring(svg)
    return svg


def main():
    owner = os.environ.get('PROFILE_OWNER', 'centurio1987')
    if not re.fullmatch(r'[A-Za-z0-9-]+', owner):
        raise ValueError('Invalid GitHub username')
    response = subprocess.run(
        ['gh', 'api', f'users/{owner}/repos?type=owner&per_page=100', '--paginate', '--slurp'],
        check=True, capture_output=True, text=True, timeout=120)
    stats, languages = summarize(json.loads(response.stdout))
    day = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    # Complete retrieval, parsing and rendering before replacing any existing card.
    outputs = {
        'stats.svg': card('Public GitHub activity', stats, 'Public, owned, non-fork repositories', day),
        'top-langs.svg': card('Primary languages', languages, 'Repository counts, not code volume or proficiency', day),
    }
    for name, svg in outputs.items():
        target = ROOT / 'profile' / name
        temporary = target.with_suffix('.svg.tmp')
        temporary.write_text(svg, encoding='utf-8')
        temporary.replace(target)


if __name__ == '__main__':
    main()
