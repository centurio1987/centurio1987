import datetime as dt
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET
import update_stats as stats


class ContributionTests(unittest.TestCase):
    def fixture(self):
        start = dt.date(2026, 1, 1)
        end = start + dt.timedelta(days=30)
        days = [{'date': (start + dt.timedelta(days=i)).isoformat(),
                 'contributionCount': 2 if i in (0, 30) else 0} for i in range(31)]
        collection = dict(totalCommitContributions=2, totalIssueContributions=0,
                          totalPullRequestContributions=1, totalPullRequestReviewContributions=0,
                          contributionCalendar=dict(totalContributions=4, weeks=[dict(contributionDays=days)]))
        return {'data': {'user': {'contributionsCollection': collection}}}, start, end

    def test_activity_and_30_day_boundary(self):
        payload, start, end = self.fixture()
        rows = dict(stats.contribution_rows(payload, start, end))
        self.assertEqual(rows['Total contributions'], 4)
        self.assertEqual(rows['Commits'], 2)
        self.assertEqual(rows['Active days'], 2)
        self.assertEqual(rows['Contributions in last 30 days'], 2)
        self.assertEqual(rows['Pull request reviews'], 0)

    def test_partial_and_error_responses_rejected(self):
        payload, start, end = self.fixture()
        with self.assertRaises(ValueError):
            stats.contribution_rows(dict(payload, errors=[{'message': 'forbidden'}]), start, end)
        c = payload['data']['user']['contributionsCollection']
        c['totalCommitContributions'] = None
        with self.assertRaises(ValueError):
            stats.contribution_rows(payload, start, end)
        c['totalCommitContributions'] = 2
        c['contributionCalendar']['weeks'][0]['contributionDays'].pop()
        with self.assertRaises(ValueError):
            stats.contribution_rows(payload, start, end)

    def test_api_failure_preserves_both_cards(self):
        for response in [subprocess.CalledProcessError(1, 'gh'),
                         subprocess.TimeoutExpired('gh', 120),
                         subprocess.CompletedProcess('gh', 0, stdout=json.dumps({'errors': [{'message': 'denied'}]}))]:
            with self.subTest(response=response), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'profile').mkdir()
                for name in ('stats.svg', 'top-langs.svg'):
                    (root / 'profile' / name).write_text('previous valid snapshot')
                with patch.object(stats, 'ROOT', root), patch.object(stats.subprocess, 'run', side_effect=[
                    subprocess.CompletedProcess('gh', 0, stdout='[[]]'), response]):
                    with self.assertRaises((ValueError, subprocess.SubprocessError)):
                        stats.main()
                for file in (root / 'profile').iterdir():
                    self.assertEqual(file.read_text(), 'previous valid snapshot')

    def test_svg_escapes_text(self):
        ET.fromstring(stats.card('A&B', [('C<D', 3)], 'note', '2026-09-15'))


if __name__ == '__main__':
    unittest.main()
