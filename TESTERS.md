# JellyRip Live Rip Smoke Test Worksheet (One Page)

Use this during live ripping. Mark pass or fail for each line, then
attach this sheet (or copy/paste results) into a GitHub issue.

Current unstable test target: `v1.0.17`

Issue tracker: [GitHub Issues](https://github.com/unexpear/JellyRip/issues)

Test build/version: ____________________
Date: ____________________
Tester: ____________________
Drive model: ____________________
Disc type: [ ] DVD  [ ] Blu-ray  [ ] 4K UHD
Disc title: ____________________

## A) Smart Rip (single movie disc)

| Check | Pass | Fail | Notes |
| --- | --- | --- | --- |
| Scan starts (Scanning disc...) | [ ] | [ ] | |
| Title scoring appears (Title scores + BEST) | [ ] | [ ] | |
| Smart selection logged (Smart Rip selected: Title X) | [ ] | [ ] | |
| Rip starts and progress updates (Ripping: X%) | [ ] | [ ] | |
| MakeMKV exit code logged | [ ] | [ ] | |
| Output file found and moved | [ ] | [ ] | |
| Session summary at end | [ ] | [ ] | |

## B) Manual Rip (TV or Movie interactive)

| Check | Pass | Fail | Notes |
| --- | --- | --- | --- |
| Disc loop starts (--- Disc N ---) | [ ] | [ ] | |
| Manual title selection accepted | [ ] | [ ] | |
| Selected size logged | [ ] | [ ] | |
| Rip completed (Ripping complete.) | [ ] | [ ] | |
| Analysis runs and files listed | [ ] | [ ] | |
| Move phase completes and temp cleanup occurs | [ ] | [ ] | |
| No unexpected aborts/errors | [ ] | [ ] | |

## C) Unattended Flow

### C1) Unattended Single

| Check | Pass | Fail | Notes |
| --- | --- | --- | --- |
| Session starts (Unattended single-disc mode started.) | [ ] | [ ] | |
| Temp path created and logged | [ ] | [ ] | |
| Rip attempts run and complete | [ ] | [ ] | |
| Completion logged with file count | [ ] | [ ] | |

### C2) Unattended Series

| Check | Pass | Fail | Notes |
| --- | --- | --- | --- |
| Series metadata logged (Series, Seasons) | [ ] | [ ] | |
| Season and disc markers logged | [ ] | [ ] | |
| Duplicate/fingerprint prompts behave correctly | [ ] | [ ] | |
| Each disc completion logged with file count | [ ] | [ ] | |
| Final completion or stop state is clear | [ ] | [ ] | |

## D) Failure/Recovery Signals

| Check | Pass | Fail | Notes |
| --- | --- | --- | --- |
| Retry path logs appear when expected | [ ] | [ ] | |
| Low-space warning/block behaves correctly | [ ] | [ ] | |
| Abort button stops work quickly and safely | [ ] | [ ] | |
| Session summary includes warnings/failures when present | [ ] | [ ] | |

## E) Attach to Issue

1. Copy/paste key log excerpt around the first failure event.
2. Include exact mode (Smart Rip / Manual / Unattended Single / Unattended Series).
3. Include this worksheet with checked pass/fail boxes.
4. Include disc type, drive model, and build/version.

Overall result: [ ] PASS  [ ] FAIL
Blocking issue IDs (if any): __________________________________________
