"""Fetch public Armory snapshots. No third-party dependencies or authentication."""
import argparse
import copy
import json
import logging
import math
import os
from pathlib import Path
import re
import socket
import tempfile
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

LOG = logging.getLogger("armory")
MAX_BYTES = 5 * 1024 * 1024
METADATA = {"fetchedAt", "fromCache"}
RESERVED = {"characters.json", "update-status.json"}


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def strict_json(text):
    def pairs(entries):
        result = {}
        for key, value in entries:
            if key in result:
                raise ValueError("Duplicate JSON key: " + key)
            result[key] = value
        return result

    def invalid(value):
        raise ValueError("Invalid JSON constant: " + value)
    return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid)


def read_json(path):
    return strict_json(path.read_text(encoding="utf-8"))


def timestamp(value):
    if value is None:
        return None
    if type(value) in (int, float):
        if not math.isfinite(value) or value < 0:
            raise ValueError("Invalid Unix millisecond timestamp")
        return datetime.fromtimestamp(value / 1000, timezone.utc).timestamp()
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("Timestamp must include timezone")
        if parsed.timestamp() < 0:
            raise ValueError("Timestamp predates Unix epoch")
        return parsed.timestamp()
    raise ValueError("Timestamp must be Unix milliseconds or ISO 8601")


def load_config(path):
    config = read_json(path)
    if not isinstance(config, list) or not config:
        raise ValueError("Configuration must be a nonempty array")
    outputs = set()
    identities = set()
    for c in config:
        if not isinstance(c, dict):
            raise ValueError("Character configuration must be an object")
        for key in ("name", "region", "realm", "game_version", "output"):
            if not isinstance(c.get(key), str) or not c[key].strip():
                raise ValueError("Missing configuration field: " + key)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _'-]*", c["name"]):
            raise ValueError("Invalid character name")
        for key in ("region", "realm", "game_version"):
            if not re.fullmatch(r"[a-z0-9-]+", c[key]):
                raise ValueError("Invalid URL component: " + key)
        output = c["output"]
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*[.]json", output) or output in RESERVED or output in outputs:
            raise ValueError("Invalid, reserved or duplicate output: " + output)
        identity = tuple(c[k].casefold() for k in ("name", "region", "realm", "game_version"))
        if identity in identities:
            raise ValueError("Duplicate character identity")
        outputs.add(output)
        identities.add(identity)
    return config


def endpoint(c):
    parts = (c["game_version"], c["region"], c["realm"], c["name"].replace(" ", "-"))
    return "https://armorywowforever.com/api/character/" + "/".join(quote(p, safe="") for p in parts) + "?refresh=1&lang=en"


def retry_delay(headers, attempt):
    delay = 2 ** attempt
    raw = headers.get("Retry-After") if headers else None
    if raw:
        try:
            delay = max(delay, float(raw))
        except ValueError:
            try:
                delay = max(delay, parsedate_to_datetime(raw).timestamp() - time.time())
            except (ValueError, TypeError, OverflowError):
                pass
    return min(60, max(0, delay))


def fetch(c, opener=urlopen, sleep=time.sleep):
    request = Request(endpoint(c), headers={"Accept": "application/json", "User-Agent": "wow-forever-armory-updater/1.0"})
    for attempt in range(3):
        try:
            with opener(request, timeout=30) as response:
                raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValueError("Response exceeds 5 MiB")
            return strict_json(raw.decode("utf-8"))
        except HTTPError as error:
            if error.code == 403:
                raise ValueError("HTTP 403: access denied; possible Cloudflare restriction. No bypass attempted.") from error
            if (error.code != 429 and not 500 <= error.code <= 599) or attempt == 2:
                raise ValueError("HTTP " + str(error.code)) from error
            delay = retry_delay(error.headers, attempt)
            LOG.warning("HTTP %s; retrying in %.1fs", error.code, delay)
        except (URLError, TimeoutError, socket.timeout, ConnectionError, OSError) as error:
            if attempt == 2:
                raise ValueError("Network request failed: " + str(error)) from error
            delay = 2 ** attempt
            LOG.warning("Network failure; retrying in %ss", delay)
        sleep(delay)


def positive_int(value):
    return type(value) is int and value > 0


def validate(data, c):
    if not isinstance(data, dict):
        raise ValueError("Character response must be an object")
    for field, expected in (("name", c["name"]), ("region", c["region"]), ("server", c["realm"])):
        if not isinstance(data.get(field), str) or data[field].casefold() != expected.casefold():
            raise ValueError("Character identity mismatch: " + field)
    if type(data.get("level")) is not int or not 1 <= data["level"] <= 60:
        raise ValueError("Character level must be an integer from 1 to 60")
    for field in ("race", "className"):
        if not isinstance(data.get(field), str) or not data[field].strip():
            raise ValueError("Missing " + field)
    if not isinstance(data.get("items"), list) or not isinstance(data.get("talents"), list):
        raise ValueError("items and talents must be arrays")
    stats = data.get("stats")
    if not isinstance(stats, dict) or not stats or not any(type(v) in (int, float) for v in stats.values()):
        raise ValueError("stats must contain numeric attributes")
    for key, value in stats.items():
        if type(value) in (int, float) and not math.isfinite(value):
            raise ValueError("Nonfinite stat: " + key)
        if value is None or isinstance(value, (dict, list, bool)):
            raise ValueError("Invalid stat: " + key)
    slots = set()
    for item in data["items"]:
        if not isinstance(item, dict) or not positive_int(item.get("id")) or not isinstance(item.get("slot"), str) or not item["slot"]:
            raise ValueError("Invalid equipped item")
        if item["slot"] in slots:
            raise ValueError("Duplicate equipment slot")
        slots.add(item["slot"])
    for spec in data["talents"]:
        if not isinstance(spec, dict) or not isinstance(spec.get("trees"), list):
            raise ValueError("Invalid talent specialization")
        for tree in spec["trees"]:
            if not isinstance(tree, dict) or not isinstance(tree.get("talents"), list) or type(tree.get("points")) is not int or tree["points"] < 0:
                raise ValueError("Invalid talent tree")
            for talent in tree["talents"]:
                if not isinstance(talent, dict) or not positive_int(talent.get("id")) or not positive_int(talent.get("rank")):
                    raise ValueError("Invalid talent rank")
    for field in ("fetchedAt", "lastLogin"):
        try:
            timestamp(data.get(field))
        except (ValueError, OverflowError, OSError) as error:
            raise ValueError("Invalid " + field + ": " + str(error)) from error
    if "fromCache" in data and type(data["fromCache"]) is not bool:
        raise ValueError("fromCache must be boolean")


def character_data(data):
    return {k: v for k, v in data.items() if k not in METADATA}


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False, newline="\n") as handle:
            temp = handle.name
            json.dump(data, handle, indent=2, ensure_ascii=False, allow_nan=False)
            handle.write("\n")
        os.replace(temp, path)
    finally:
        if temp and os.path.exists(temp):
            os.unlink(temp)


def update(root, config, fetcher=fetch, now=utc_now):
    status_path = root / "update-status.json"
    status = read_json(status_path) if status_path.exists() else {}
    if not isinstance(status, dict):
        raise ValueError("Existing status must be an object")
    original_status = copy.deepcopy(status)
    failed = False
    for c in config:
        key = c["output"]
        checked = now()
        previous = status.get(key, {})
        entry = dict(previous)
        entry["name"] = c["name"]
        try:
            path = root / key
            if path.is_symlink():
                raise ValueError("Snapshot output must not be a symlink")
            old = None
            if path.exists():
                try:
                    old = read_json(path)
                    validate(old, c)
                except (ValueError, UnicodeError):
                    LOG.warning("%s: existing snapshot is invalid; valid retrieval may repair it", c["name"])
                    old = None
            data = fetcher(c)
            validate(data, c)
            old_time = timestamp(old.get("fetchedAt")) if old else None
            new_time = timestamp(data.get("fetchedAt"))
            if old_time is not None and new_time is not None and new_time < old_time:
                raise ValueError("Source fetchedAt regressed; preserving last good snapshot")
            changed = old is None or character_data(old) != character_data(data)
            advanced = new_time is not None and (old_time is None or new_time > old_time)
            provenance_changed = old is not None and old.get("fromCache") != data.get("fromCache")
            publish = changed or advanced or provenance_changed
            if changed:
                outcome = "character_changed"
            elif data.get("fromCache") is True:
                outcome = "cached_unchanged"
            elif data.get("fromCache") is False:
                outcome = "source_refreshed_unchanged"
            else:
                outcome = "unchanged_freshness_unknown"
            entry.update(outcome=outcome, source_fetched_at=data.get("fetchedAt"),
                         source_from_cache=data.get("fromCache"), character_changed=changed,
                         last_error=None, last_error_at=None)
            meaningful = publish or any(entry.get(k) != previous.get(k) for k in entry)
            if publish:
                atomic_write(path, data)
                entry["last_successful_published_snapshot"] = checked
            else:
                entry.setdefault("last_successful_published_snapshot", None)
            if meaningful or not previous:
                entry["last_successful_check"] = checked
            LOG.info("%s: %s; fetchedAt=%s fromCache=%s lastLogin=%s candidate_written=%s check=%s; upstream freshness not guaranteed",
                     c["name"], outcome, data.get("fetchedAt"), data.get("fromCache"), data.get("lastLogin"), publish, checked)
        except (ValueError, OSError, UnicodeError) as error:
            failed = True
            message = str(error)
            entry.update(outcome="failed", character_changed=False, last_error=message)
            entry.setdefault("last_successful_check", None)
            entry.setdefault("last_successful_published_snapshot", None)
            entry.setdefault("source_fetched_at", None)
            entry.setdefault("source_from_cache", None)
            if previous.get("last_error") != message or previous.get("outcome") != "failed":
                entry["last_error_at"] = checked
            LOG.error("%s: %s; check=%s", c["name"], message, checked)
        status[key] = entry
    if status != original_status:
        atomic_write(status_path, status)
    return 1 if failed else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("characters.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("."))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        return update(args.output_dir, load_config(args.config))
    except (ValueError, OSError, UnicodeError) as error:
        LOG.error("Updater failed: %s", error)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
