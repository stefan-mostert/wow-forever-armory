import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from urllib.error import HTTPError, URLError

import update_armory as armory

CONFIG = {"name": "Otheos Stormward", "region": "us", "realm": "normal",
          "game_version": "forever", "output": "otheos.json"}
BASE = {"name": "Otheos Stormward", "region": "us", "server": "normal",
        "race": "Human", "className": "Paladin", "level": 23,
        "items": [], "stats": {"health": 829, "powerType": "Mana"},
        "talents": [], "fetchedAt": 1794513382326, "fromCache": True,
        "lastLogin": 1794478315000, "unknownField": {"keep": True}}


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.clock = Mock(return_value="2026-11-13T00:00:00Z")

    def run_update(self, data=None, config=None, fetcher=None):
        return armory.update(self.root, config or [CONFIG],
                             fetcher or Mock(return_value=copy.deepcopy(data or BASE)),
                             self.clock)

    def saved(self, name="otheos.json"):
        return armory.read_json(self.root / name)

    def test_success_preserves_full_response(self):
        self.assertEqual(self.run_update(), 0)
        self.assertEqual(self.saved(), BASE)
        self.assertTrue(self.saved("update-status.json")["otheos.json"]["character_changed"])

    def test_character_update(self):
        self.run_update()
        data = copy.deepcopy(BASE)
        data["stats"]["health"] += 10
        self.assertEqual(self.run_update(data), 0)
        self.assertEqual(self.saved()["stats"]["health"], 839)

    def test_unchanged_cached_does_not_rewrite(self):
        self.run_update()
        self.run_update()  # record transition from changed to unchanged
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        self.clock.return_value = "2026-11-13T01:00:00Z"
        self.run_update()
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.iterdir()})
        self.assertEqual(self.saved("update-status.json")["otheos.json"]["outcome"], "cached_unchanged")

    def test_new_fetch_time_cached_not_character_change(self):
        self.run_update()
        data = dict(BASE, fetchedAt=BASE["fetchedAt"] + 1000)
        self.run_update(data)
        entry = self.saved("update-status.json")["otheos.json"]
        self.assertFalse(entry["character_changed"])
        self.assertEqual(entry["outcome"], "cached_unchanged")
        self.assertEqual(self.saved(), data)

    def test_uncached_unchanged(self):
        self.run_update()
        self.run_update(dict(BASE, fromCache=False))
        self.assertEqual(self.saved("update-status.json")["otheos.json"]["outcome"],
                         "source_refreshed_unchanged")

    def test_metadata_unknown(self):
        data = dict(BASE)
        del data["fromCache"]
        self.run_update(data)
        self.run_update(data)
        self.assertEqual(self.saved("update-status.json")["otheos.json"]["outcome"],
                         "unchanged_freshness_unknown")

    def test_formatting_only_does_not_rewrite_snapshot(self):
        path = self.root / "otheos.json"
        text = json.dumps(BASE, separators=(",", ":"))
        path.write_text(text)
        self.run_update()
        self.assertEqual(path.read_text(), text)

    def test_failure_preserves_last_good_and_repeated_error_is_stable(self):
        self.run_update()
        original = (self.root / "otheos.json").read_bytes()
        failed = Mock(side_effect=ValueError("HTTP 503"))
        self.assertEqual(self.run_update(fetcher=failed), 1)
        first_status = (self.root / "update-status.json").read_bytes()
        self.clock.return_value = "2026-11-13T02:00:00Z"
        self.assertEqual(self.run_update(fetcher=failed), 1)
        self.assertEqual(first_status, (self.root / "update-status.json").read_bytes())
        self.assertEqual(original, (self.root / "otheos.json").read_bytes())
        self.assertIsNotNone(self.saved("update-status.json")["otheos.json"]["last_successful_check"])

    def test_recovery_clears_error(self):
        self.run_update()
        self.run_update(fetcher=Mock(side_effect=ValueError("failed")))
        self.run_update()
        self.assertIsNone(self.saved("update-status.json")["otheos.json"]["last_error"])

    def test_missing_data_and_identity_preserve_snapshot(self):
        self.run_update()
        for field in ("items", "stats", "talents", "name", "level", "race", "className", "region", "server"):
            with self.subTest(field=field):
                data = dict(BASE)
                del data[field]
                self.assertEqual(self.run_update(data), 1)
                self.assertEqual(self.saved(), BASE)

    def test_invalid_structures(self):
        bad_values = {"level": [True, 0, 61, "23"], "stats": [{}, [], {"health": None}],
                      "items": [None, [{}]], "talents": [None, [{}]],
                      "fromCache": ["true", None], "fetchedAt": [-1, "yesterday", True],
                      "lastLogin": ["2026-11-13T00:00:00"]}
        for field, values in bad_values.items():
            for value in values:
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    armory.validate(dict(BASE, **{field: value}), CONFIG)

    def test_timestamp_formats(self):
        self.assertEqual(armory.timestamp("1970-01-01T00:00:01Z"), 1)
        self.assertEqual(armory.timestamp(1000), 1)
        armory.validate(dict(BASE, fetchedAt=None, lastLogin=None), CONFIG)

    def test_timestamp_regression_preserves_data(self):
        self.run_update()
        self.assertEqual(self.run_update(dict(BASE, fetchedAt=BASE["fetchedAt"] - 1, level=24)), 1)
        self.assertEqual(self.saved(), BASE)

    def test_multiple_characters_failure_isolated(self):
        other = dict(CONFIG, name="Tiffily Frostveil", output="tiffily.json")
        fetcher = Mock(side_effect=[ValueError("denied"), dict(BASE, name=other["name"])])
        self.assertEqual(self.run_update(config=[CONFIG, other], fetcher=fetcher), 1)
        self.assertEqual(self.saved("tiffily.json")["name"], other["name"])
        self.assertEqual(fetcher.call_count, 2)

    def test_config_validation(self):
        path = self.root / "characters.json"
        for config in ([], [CONFIG, CONFIG], [dict(CONFIG, output="../x.json")],
                       [dict(CONFIG, output="update-status.json")],
                       [dict(CONFIG, region="us/evil")]):
            path.write_text(json.dumps(config))
            with self.assertRaises(ValueError):
                armory.load_config(path)
        path.write_text(json.dumps([CONFIG]))
        self.assertEqual(armory.load_config(path), [CONFIG])

    def response(self, body):
        response = Mock()
        response.__enter__ = Mock(return_value=io.BytesIO(body))
        response.__exit__ = Mock(return_value=False)
        return response

    def test_request_refresh_timeout(self):
        opener = Mock(return_value=self.response(json.dumps(BASE).encode()))
        self.assertEqual(armory.fetch(CONFIG, opener=opener), BASE)
        request = opener.call_args.args[0]
        self.assertIn("Otheos-Stormward?refresh=1&lang=en", request.full_url)
        self.assertEqual(opener.call_args.kwargs["timeout"], 30)

    def test_invalid_json_and_challenge_html(self):
        for body in (b"not json", b"<html>challenge</html>", b'{"a":1,"a":2}', b'{"a":NaN}'):
            with self.subTest(body=body), self.assertRaises(ValueError):
                armory.fetch(CONFIG, opener=Mock(return_value=self.response(body)))

    def test_http_failures_and_retry_limits(self):
        for code, expected in ((403, 1), (404, 1), (429, 3), (503, 3)):
            opener = Mock(side_effect=HTTPError("url", code, "error", {}, None))
            sleep = Mock()
            with self.subTest(code=code), self.assertRaises(ValueError):
                armory.fetch(CONFIG, opener=opener, sleep=sleep)
            self.assertEqual(opener.call_count, expected)
            self.assertEqual(sleep.call_count, expected - 1)

    def test_retry_recovers_and_honors_rate_limit(self):
        opener = Mock(side_effect=[HTTPError("url", 429, "limit", {"Retry-After": "999"}, None),
                                   self.response(json.dumps(BASE).encode())])
        sleep = Mock()
        self.assertEqual(armory.fetch(CONFIG, opener=opener, sleep=sleep), BASE)
        sleep.assert_called_once_with(60)

    def test_network_backoff(self):
        opener = Mock(side_effect=URLError("offline"))
        sleep = Mock()
        with self.assertRaises(ValueError):
            armory.fetch(CONFIG, opener=opener, sleep=sleep)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [1, 2])

    def test_real_repository_fixture_validates(self):
        fixture = Path(__file__).parent / "fixtures" / "otheos.json"
        armory.validate(armory.read_json(fixture), CONFIG)


if __name__ == "__main__":
    unittest.main()
