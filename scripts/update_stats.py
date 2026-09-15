"""Render contribution metrics and public language metadata as local SVGs."""
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

QUERY = '''query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      totalCommitContributions totalIssueContributions
      totalPullRequestContributions totalPullRequestReviewContributions
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date contributionCount } }
      }
    }
  }
}'''


def contribution_rows(payload, start, end):
    if payload.get('errors'):
        raise ValueError('GitHub returned GraphQL errors; keeping previous cards')
    collection = payload['data']['user']['contributionsCollection']
    calendar = collection['contributionCalendar']

    def count(value):
        if type(value) is not int or value < 0:
            raise ValueError('Missing or invalid contribution count')
        return value

    days = {}
    for week in calendar['weeks']:
        for item in week['contributionDays']:
            day = datetime.date.fromisoformat(item['date'])
            if start <= day <= end:
                if day in days:
                    raise ValueError('Duplicate calendar date')
                days[day] = count(item['contributionCount'])
    if len(days) != (end - start).days + 1:
        raise ValueError('Incomplete contribution calendar')
    total = count(calendar['totalContributions'])
    if total != sum(days.values()):
        raise ValueError('Calendar total does not match daily contributions')
    return [
        ('Total contributions', total),
        ('Commits', count(collection['totalCommitContributions'])),
        ('Pull requests opened', count(collection['totalPullRequestContributions'])),
        ('Pull request reviews', count(collection['totalPullRequestReviewContributions'])),
        ('Issues opened', count(collection['totalIssueContributions'])),
        ('Active days', sum(value > 0 for value in days.values())),
        ('Contributions in last 30 days', sum(value for day, value in days.items()
                                             if day >= end - datetime.timedelta(days=29))),
    ]


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
    _, languages = summarize(json.loads(response.stdout))
    now = datetime.datetime.now(datetime.timezone.utc)
    end = now.date()
    start = end - datetime.timedelta(days=364)
    response = subprocess.run(
        ['gh', 'api', 'graphql', '-f', f'query={QUERY}', '-f', f'login={owner}',
         '-f', f'from={start.isoformat()}T00:00:00Z', '-f', f'to={now.isoformat()}'],
        check=True, capture_output=True, text=True, timeout=120)
    stats = contribution_rows(json.loads(response.stdout), start, end)
    day = end.isoformat()
    # Complete retrieval, parsing and rendering before replacing any existing card.
    outputs = {
        'stats.svg': card('GitHub contributions · Last 365 days', stats,
                          f'{start} to {end} UTC · GitHub contribution rules', day),
        'top-langs.svg': card('Primary languages', languages, 'Repository counts, not code volume or proficiency', day),
    }
    for name, svg in outputs.items():
        target = ROOT / 'profile' / name
        temporary = target.with_suffix('.svg.tmp')
        temporary.write_text(svg, encoding='utf-8')
        temporary.replace(target)


if __name__ == '__main__':
    main()
