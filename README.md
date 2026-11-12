# WoW Forever Armory snapshots

Public character equipment, talents and statistics for tools such as ChatGPT.

## Current delivery status

The [manual GitHub-hosted test](https://github.com/stefan-mostert/wow-forever-armory/actions/runs/43303688505)
successfully retrieved and validated Otheos. The source reported fromCache=false
and newer fetchedAt metadata; character information was unchanged.

Hourly scheduling (minute 23 UTC) and automatic commits are enabled by the
Phase 2 workflow after this change is merged. No home PC is required.
The first Phase 2 publication and scheduled run still need to be observed.

See [SPEC.md](SPEC.md) for the source of truth. Change it before functionality.

## How it works

characters.json defines every tracked character. update_armory.py requests each
public API endpoint using refresh=1&lang=en, validates the response, compares it to
the previous snapshot and atomically writes valid changes. The complete response,
including unknown fields, is preserved. A failed character leaves its snapshot
untouched while other characters continue. Any failure produces a nonzero exit code.

Only Python 3.12 and its standard library are required. Requests time out after
30 seconds, use at most three attempts and retry network errors, 429 and 5xx with
exponential backoff. Retry-After is honored up to 60 seconds. HTTP 403 is not retried.
Responses over 5 MiB, malformed JSON, wrong identities, incomplete data and
regressing fetchedAt timestamps are rejected. Empty items/talents arrays are valid.

## Run locally

From the repository directory:

```sh
python -m unittest discover -s tests -v
python update_armory.py
```

To keep candidate outputs separate, first copy the existing snapshots and status
into a candidate directory, then run:

```sh
python update_armory.py --config characters.json --output-dir candidate
```

Without copying the existing files first, this is treated as an initial snapshot.

## Run GitHub Actions

Open Actions, select **Update Armory**, choose **Run workflow**, select the
default branch and run it. It also runs hourly at minute 23 UTC. GitHub may delay
scheduled runs, so this is not an exact hourly service guarantee.

The workflow tests first, fetches candidates and publishes changed snapshots and
status using the built-in GITHUB_TOKEN with contents:write. It stages only
configured output filenames and update-status.json, skips empty commits and
serializes updater runs. No secrets, cookies or personal token are required.
Only the default branch can run the updater.

Failures still publish changed diagnostic status where possible while retaining
the last good snapshots. A failed retrieval or push makes the workflow fail.
Concurrent human pushes can cause a safe push rejection; rerun on the latest
default branch. Branch rules may require allowing the bot to publish or changing
the design to publication pull requests.

Download the armory-update-RUN_ID artifact to inspect candidate JSON,
update-status.json and updater.log. The publication step reports whether it made
a commit. A green Phase 2 run means all characters were checked and publication
completed or there were no meaningful changes.

## Add a character

Add an entry to characters.json:

```json
{
  "name": "Tiffily Frostveil",
  "region": "us",
  "realm": "normal",
  "game_version": "forever",
  "output": "tiffily.json"
}
```

Use the exact Armory name. Outputs must be unique lowercase root-level JSON
filenames. No Python or workflow changes are needed. Run the tests and a manual
live workflow to confirm the character exists.

## Caching and freshness

refresh=1 asks the Armory to refresh. It does not guarantee fresh Blizzard data.
fetchedAt is source retrieval metadata, fromCache describes source caching and
lastLogin describes character activity. Neither a newer fetchedAt nor lastLogin
proves equipment, stats or talents reflect the latest game state.

Comparisons ignore only top-level fetchedAt and fromCache. A new fetchedAt or
changed cache provenance may publish useful metadata without claiming a character
change. An identical cached response preserves the snapshot. JSON whitespace and
object-key order do not cause snapshot changes.

Status outcomes:
* character_changed: character information differs or this is the first snapshot.
* source_refreshed_unchanged: same character information, fromCache is false.
* cached_unchanged: same character information, fromCache is true.
* unchanged_freshness_unknown: same information and no cache indicator.
* failed: retrieval, validation or local writing failed.

update-status.json records per-character check/publication times, source metadata,
change detection and the last error. On failure source metadata refers to the
last accepted response. Status is written on meaningful state transitions or
snapshot changes, not every check. Persisted last_successful_check is the most
recent *recorded* success; Actions logs show routine checks. One extra status
transition may be written when a changed result becomes unchanged.

last_successful_published_snapshot is set when a snapshot candidate is written.
The publication step then commits it. If pushing fails, public files stay unchanged;
inspect the failed run and do not treat artifact timestamps as proof of publication.

## Troubleshooting

* HTTP 403 / HTML instead of JSON: possible Cloudflare restriction. No bypass or
  session cookies are used. A failure here preserves the existing snapshot.
* HTTP 429: limited retries respect Retry-After with a 60 second cap. Try later.
* HTTP 5xx / timeouts: temporary service failures get bounded retries; inspect logs.
* Validation failure: inspect the source schema and configuration. Update SPEC.md,
  validation and mocked tests together if the upstream schema legitimately changes.
* Invalid existing snapshot: a valid new response may repair it.
* fetchedAt regression: inspect the source; the updater keeps the newer snapshot.
* Workflow unavailable: it must exist on the default branch for manual dispatch.
* No artifact: inspect checkout/setup/tests failures; retrieval may not have started.

Local DNS resolution failed during setup. The linked manual GitHub-hosted run
subsequently confirmed valid API access from a real runner. If GitHub-hosted requests are denied, options are asking the
Armory operator for an approved API route, using an approved cloud runner if they
permit it, or retaining manual snapshot uploads. No security workaround is included.

## Published JSON

Current committed snapshot:
https://raw.githubusercontent.com/stefan-mostert/wow-forever-armory/main/otheos.json

Status (available after the first Phase 2 publication):
https://raw.githubusercontent.com/stefan-mostert/wow-forever-armory/main/update-status.json

Replace otheos.json with another configured output filename. Raw GitHub responses
can themselves be cached, so consumers should read the source metadata and status.
