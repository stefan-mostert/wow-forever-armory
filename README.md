# WoW Forever Armory snapshots

Public character equipment, talents and statistics for tools such as ChatGPT.

## Current delivery status

Phase 1 is implemented: manual retrieval, validation, mocked tests and downloadable
candidate artifacts. Hourly scheduling and automatic commits are deliberately
disabled until a real GitHub-hosted manual run successfully retrieves valid data.
No successful live runner test has been established by the implementation itself.

See [SPEC.md](SPEC.md) for the source of truth. Change the specification before
changing functionality.

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

Merge the Phase 1 pull request to main so GitHub registers the manual workflow.
Open Actions, select **Update Armory (Phase 1)**, choose **Run workflow**, select
main and run it. No secrets, cookies or personal token are needed.

The workflow runs mocked tests before calling the live API. Download the
armory-phase1-RUN_ID artifact to inspect candidate JSON, update-status.json and
updater.log. A green run means all configured characters were retrieved and
validated. On failure, inspect the failed step and available diagnostics.
Candidate files are not committed in Phase 1; published raw URLs stay unchanged.

After a verified successful GitHub-hosted retrieval, Phase 2 can add:
* An hourly schedule: cron '23 * * * *' (UTC; execution may be delayed).
* A separate publication step with contents: write using GITHUB_TOKEN.
* Staging only configured snapshots plus update-status.json, skipping empty commits.
* Publishing from main only, retaining the repository-wide concurrency group.
* Failure status publication even when retrieval fails, without replacing good data.

These changes are gated on the live test and are not currently enabled.
Branch rules may require a publication PR rather than a direct push.

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

During Phase 1 last_successful_published_snapshot means the candidate was written
inside the runner, not that main or the public raw URL was updated.

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

Local network access was unavailable during initial setup. This does not establish
a Cloudflare restriction or prove GitHub-hosted API access. The manual run is the
required evidence. If GitHub-hosted requests are denied, options are asking the
Armory operator for an approved API route, using an approved cloud runner if they
permit it, or retaining manual snapshot uploads. No security workaround is included.

## Published JSON

Current committed snapshot:
https://raw.githubusercontent.com/stefan-mostert/wow-forever-armory/main/otheos.json

After status is published in Phase 2:
https://raw.githubusercontent.com/stefan-mostert/wow-forever-armory/main/update-status.json

Replace otheos.json with another configured output filename. Raw GitHub responses
can themselves be cached, so consumers should read the source metadata and status.
