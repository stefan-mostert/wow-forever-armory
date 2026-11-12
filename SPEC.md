# Armory updater specification

This specification is the source of truth. Update it before changing behavior.
Resolve ambiguous or conflicting requirements with the user before implementation.

## Configuration and retrieval
characters.json contains a nonempty array of characters with name, region, realm,
game_version and output. Outputs are unique root-level JSON filenames, excluding
configuration and status files. Paths and URL components are validated.
Request the public Armory character endpoint with refresh=1&lang=en.
Use Python 3.12 standard libraries, a 30 second request timeout, at most three
attempts, exponential backoff and bounded Retry-After handling. Retry network
errors, HTTP 429 and 5xx. Do not retry HTTP 403 or bypass challenges or use cookies.
Cap responses at 5 MiB and reject nonstandard JSON constants and duplicate keys.

## Validation and preservation
Require matching name, region and server, an integer level from 1 through 60,
nonempty race and className, items and talents arrays, and nonempty stats object.
Empty item and talent arrays are valid. Validate populated items (positive integer
id and unique nonempty slot), talent specs/trees/ranks and finite numeric stats.
Timestamps may be absent/null, nonnegative Unix milliseconds or timezone-qualified
ISO 8601 strings. fromCache must be boolean if present.
Keep all original response fields. Validate before atomically replacing a snapshot.
A failed character must not prevent other configured characters from being checked.
Fail the process if any character fails.

## Changes and status
Compare parsed JSON, ignoring only top-level fetchedAt and fromCache for character
changes. lastLogin is character information, never proof of freshness.
Publish when character data changes, fetchedAt advances, or cache provenance changes.
Reject a fetchedAt regression rather than replace a newer snapshot.
Distinguish character_changed, source_refreshed_unchanged,
cached_unchanged, unchanged_freshness_unknown, and failed.
Never claim refresh=1 guarantees upstream freshness.
update-status.json is keyed by output filename. Include name, outcome,
last_successful_check, last_successful_published_snapshot, source_fetched_at,
source_from_cache, character_changed, last_error and last_error_at.
Generated timestamps are UTC ISO 8601. Persist status on snapshot publication or
meaningful state transitions only. Repeated identical successes/failures log their
actual check time without timestamp-only writes. Thus persisted check times are
the latest recorded checks, not necessarily the latest workflow checks.
During Phase 1, 'published snapshot' means a locally written candidate, not a
commit to the default branch. README must make this distinction explicit.

## Delivery gates
Phase 1: mocked tests, manual-only updater workflow, artifact diagnostics even on
failure, no automatic commits or schedule. A separate PR CI workflow runs mocks.
A real GitHub-hosted manual run must fetch and validate every configured character
before Phase 2 is enabled. API availability is unverified until then.
Phase 2: add hourly cron at minute 23 and a contents:write publication step using
GITHUB_TOKEN, stage only configured snapshots and status, skip empty commits,
push only the default branch, and retain failure diagnostics. No credentials.
Use repository-wide updater concurrency to prevent overlapping publications.
GitHub scheduling can be delayed; it is not an exact hourly service guarantee.

## Phase 2 activation record
Manual GitHub-hosted run 43303688505 succeeded: live Otheos retrieval validated,
fromCache=false, fetchedAt=1794516423867, character information unchanged.
The hourly schedule and automatic publication are now approved by this gate.
Publication runs on the default branch only. After retrieval (success or failure),
copy candidate snapshots and status to the checkout and stage only configured
outputs plus status. Commit only staged changes; a failed push fails the run.
The retrieved outcome still fails the workflow if any character failed, even when
failure diagnostics were successfully committed. Tests must pass before retrieval.
Published snapshot timestamps refer to snapshot writes which are committed by the
publication step; a push failure leaves public files unchanged and fails the run.

## GitHub Actions runtime
Use Node.js 24-based official actions: checkout v7.0.1, setup-python v7.1.0
and upload-artifact v7.0.2. Python remains 3.12. These action runtime updates
do not change updater permissions, publication behavior or the hourly schedule.
