import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

import update_claude_stats as claude


class ClaudeUsageTests(unittest.TestCase):
    def test_deduplicates_responses_and_omits_private_content(self):
        day = dt.date(2026, 10, 1)
        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder) / 'private-project' / 'session.jsonl'
            log.parent.mkdir()
            base = {
                'type': 'assistant', 'timestamp': '2026-10-01T12:00:00Z',
                'sessionId': 'secret-session', 'cwd': '/secret/client',
                'message': {'id': 'msg-1', 'model': 'claude-opus-5',
                            'content': 'confidential prompt',
                            'usage': {'input_tokens': 2, 'output_tokens': 3,
                                      'cache_creation_input_tokens': 5,
                                      'cache_read_input_tokens': 7}},
            }
            earlier = dict(base, timestamp='2025-09-30T12:00:00Z')
            log.write_text('\n'.join(map(json.dumps, [base, base, earlier])) + '\n')
            totals, models, calls, sessions, monthly, cost = claude.collect(Path(folder), day, day)
            self.assertEqual((calls, sessions), (1, 1))
            self.assertEqual(sum(totals.values()), 17)
            self.assertEqual(str(cost), '0.00011975')
            output = ''.join(claude.render(totals, models, calls, sessions, monthly, cost, day, day).values())
            for svg in claude.render(totals, models, calls, sessions, monthly, cost, day, day).values():
                ET.fromstring(svg)
            self.assertNotIn('confidential', output)
            self.assertNotIn('/secret/client', output)
            self.assertNotIn('secret-session', output)
            self.assertNotIn('private-project', output)

    def test_empty_period_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                claude.collect(Path(folder), dt.date(2026, 10, 1), dt.date(2026, 10, 1))

    def test_one_hour_cache_write_rate_and_unknown_model(self):
        usage = {'cache_creation': {'ephemeral_5m_input_tokens': 0,
                                    'ephemeral_1h_input_tokens': 1_000_000}}
        values = {'input_tokens': 0, 'output_tokens': 0,
                  'cache_creation_input_tokens': 1_000_000, 'cache_read_input_tokens': 0}
        self.assertEqual(claude.api_equivalent_cost('claude-opus-5', usage, values), 10)
        with self.assertRaises(ValueError):
            claude.api_equivalent_cost('claude-unknown', usage, values)


if __name__ == '__main__':
    unittest.main()
