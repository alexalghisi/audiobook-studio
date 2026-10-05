# audiobook-studio

Producing an audiobook with TTS is mostly an editing loop: import the manuscript, generate, listen, fix a sentence or a pronunciation, generate again, export. The expensive part is synthesis, and the annoying part is waiting for a whole chapter (or book) to be redone because one word changed. audiobook-studio is a small Studio-style production tool built around that loop. Every segment's audio is addressed by what produced it, so an edit regenerates exactly the segments it touched, a re-export re-renders only the chapters that changed, and a crashed render picks up where it stopped.

![Importing a manuscript, generating, editing one paragraph so that only it regenerates, and exporting](docs/preview.gif)

It runs fully offline with espeak-ng (real, robotic speech), and switches to ElevenLabs when `ELEVENLABS_API_KEY` is set.

## How it works

**Manuscript to segments.** Markdown, plain text and EPUB are parsed into chapters of paragraphs (`backend/src/studio/domain/manuscript.py`; EPUB follows the OPF spine with the standard library only). Paragraphs are split into sentences by `domain/sentences.py`, which rejects false boundaries: honorifics and initials (`Mr. Darcy`, `J. R. R. Tolkien`), dotted acronyms, decimals, ellipses followed by lowercase, and punctuation inside dialogue (`"Stop!" she said.` stays one sentence, as does `“I went home. Then I slept,” he said.`). `domain/segmenter.py` packs whole sentences up to a character budget (800 by default). A segment never spans two paragraphs and never ends mid-sentence; only a sentence longer than the provider's request limit is cut, at clause punctuation first.

**Content-addressed audio.** A segment's key is a SHA-256 over the text *after* pronunciation rules, the voice, every voice setting, the provider, the model and a take number (`domain/cache_key.py`). Audio is stored under that key, and every change (a text edit, a per-segment override, a project voice change, a new pronunciation rule, an explicit "new take") just recomputes keys (`services/projects.py`). Consequences:

- Editing one paragraph invalidates one segment. Adding a rule for `Siobhan` invalidates only the segments that contain it.
- Undoing an edit, or switching the voice back, is free: the old key still has audio.
- Two segments with identical text and voice share one request; concurrent jobs in one process share in-flight requests.
- The neighbouring text sent for ElevenLabs request stitching (`previous_text`/`next_text`) is deliberately *not* part of the key. If it were, fixing one word would also invalidate the segments on either side. The trade-off is that a neighbour's prosody hint can be one edit out of date.

The bytes live in a content store on disk, written to a temp file and renamed into place (`audio/store.py`). A PostgreSQL row per key indexes them, with duration and waveform peaks computed once at write time; a row whose file has gone missing counts as a miss.

**Pronunciation dictionary.** Rules are compiled into a single regex alternation, longest term first, and applied in one pass (`domain/pronunciation.py`). An alias is never rewritten by another rule, `New York City` wins over `New York`, and whole-word matching uses lookarounds so terms like `C++` work.

**TTS providers.** One small port (`tts/base.py`), three implementations:

- `ElevenLabsProvider` (`tts/elevenlabs.py`) calls `POST /v1/text-to-speech/{voice_id}` with `xi-api-key`, `model_id`, `voice_settings` (stability, similarity, style, speed, speaker boost) and `previous_text`/`next_text`. A 429 waits for `Retry-After` (seconds or HTTP date, capped), or backs off when the header is missing, which is how ElevenLabs answers too many concurrent requests. 5xx and transport errors get exponential backoff with full jitter, other 4xx fail immediately. When retries run out, the error is still marked retryable, so the job can try that segment again later.
- `EspeakProvider` and `FormantProvider` (`tts/offline.py`) are deterministic: the same request gives the same bytes. The formant synthesizer is a fallback for machines without espeak-ng. It produces speech-like audio (voiced vowels with formants, consonant noise, real pauses) with a duration proportional to the text.
- `studio/testing/fake_elevenlabs.py` is a local stand-in for the API that validates requests like the real endpoint (key, voice id, setting ranges, text length), returns real MP3 and injects faults: scripted 429/5xx, a random failure rate, latency and a concurrency limit. The adapter's tests run against it over real HTTP.

**Jobs.** PostgreSQL holds each job and is the source of truth for its state. Redis decides which worker runs it (`jobs/queue.py`):

- Workers claim with `BLMOVE` into their own processing list and hold a lease that a heartbeat keeps alive.
- A job left in the list of a worker whose heartbeat expired goes back to the ready list. `LMOVE` is atomic, so two reapers can't both requeue it.
- Retries wait in a delayed sorted set.
- A reconcile pass re-enqueues active jobs that Redis no longer knows about, for example when the API died between committing the job and enqueueing it.
- Every final write is fenced by the attempt number, so a worker that lost its lease can't overwrite the outcome recorded by the worker that took over.
- Submitting returns `202` for a new job and `200` for the existing one: an active job with the same dedupe key is reused (a partial unique index makes that race-safe), and `Idempotency-Key` returns the original job even after it finished.
- Cancelling a job aborts in-flight synthesis requests within a quarter of a second, stops before the next chapter render, and puts unfinished segments back to "needs audio". A worker that is shut down kills any ffmpeg process it started.
- Progress and segment status changes are published on Redis pub/sub and relayed to the browser over one SSE stream per project. The stream subscribes before it sends its snapshot, so an event can arrive twice but never gets lost.

**Rendering.** `render/renderer.py` stitches a chapter's segments with pauses: 0.3 s between segments of a paragraph, 0.8 s between paragraphs, 0.6 s of room tone at the head and 2 s at the tail. It then normalizes in two `loudnorm` passes to -18 LUFS integrated with a -3 dBTP ceiling. The measured first pass allows a linear gain when that is enough to reach the target. The same pass is encoded twice: a 192 kbps CBR MP3 with ID3 tags (the ACX format), and AAC. The M4B is the chapters' AAC streams joined by stream copy, plus an ffmetadata chapter marker per chapter. Every output is keyed by its inputs, and a JSON sidecar written last marks a chapter as complete. A re-export after an edit re-renders one chapter, and an export that crashed resumes from the chapters it had finished. Tests check the output independently with `ffprobe` (codec, bitrate, tags, chapter titles and start times) and ffmpeg's `ebur128` meter (integrated loudness within 1 LU of the target, true peak at or below -2.5 dBTP).

**UI.** React 19 (`web/src`): a chapter list with a status bar per chapter, and a segment list with inline editing (double-click or Edit, Ctrl+Enter to save), per-segment voice/speed/stability overrides, generate and new take. Status badges update live from SSE (needs audio, queued, generating, cached, failed). The page also has project voice settings, the pronunciation dictionary, and an export panel with live progress, loudness per chapter and downloads. The player draws its waveform on a canvas from peaks computed on the server. Audio URLs include the content key, so the browser can cache them forever. A status event for a key the segment no longer has is treated as stale, and the chapter is reloaded from the server.

## Numbers

`bench/render_book.py` runs the real pipeline (PostgreSQL, Redis, a worker, espeak-ng, ffmpeg) on a generated 20-chapter book: 12,645 words, 160 segments, 77 minutes of audio. It exports, edits one paragraph, exports again, then exports once more with no changes. Measured on a 2-vCPU VM shared with other builds, so treat the times as rough. The results are in `bench/results.json`.

| Run | Wall time | Synthesis requests | Cache hit rate | Chapters rendered |
| --- | --- | --- | --- | --- |
| First export | 231 s (synthesis 24 s, render 205 s, M4B 1.3 s) | 160 | 0% | 20 of 20 |
| After editing one paragraph | 20.6 s | 1 (159 saved) | 99.4% | 1 of 20 |
| No changes | 0.6 s | 0 | 100% | 0 of 20 |

- All 20 chapters measured between -18.53 and -18.38 LUFS, with true peak at -3.0 dBTP.
- Building the M4B by stream copy from per-chapter AAC brought the one-paragraph re-export down from 128 s to 21 s; before that change, re-encoding the whole book dominated.
- On the first export, time goes almost entirely into loudness normalization and encoding, not synthesis, because espeak-ng is fast. With ElevenLabs, synthesis time is network-bound and capped by the configured concurrency (4 by default).

## Tech stack

Python 3.13, FastAPI, Pydantic v2, SQLAlchemy 2 (async, asyncpg), Alembic, PostgreSQL 16, Redis (own reliable queue and pub/sub on `redis-py`), httpx, NumPy, ffmpeg/ffprobe, espeak-ng. React 19, TypeScript (strict, `noUncheckedIndexedAccess`), Vite, Vitest, Testing Library. mypy `--strict`, ruff. Playwright and Pillow for the preview capture.

## Running it

You need PostgreSQL, Redis, ffmpeg, Node 22 and Python 3.13. espeak-ng is optional (`apt install espeak-ng`); without it the offline provider falls back to the formant synthesizer.

```bash
python3.13 -m venv .venv
.venv/bin/pip install -e backend --group 'backend/pyproject.toml:dev'

export STUDIO_DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/audiobook_studio
export STUDIO_REDIS_URL=redis://localhost:6379/0
export STUDIO_DATA_DIR=$PWD/data
(cd backend && ../.venv/bin/alembic upgrade head)

.venv/bin/studio-api        # http://127.0.0.1:8700
.venv/bin/studio-worker     # as many as you like

cd web && npm ci && npm run dev   # http://localhost:5195, proxies /api to :8700
```

`examples/the-keeper.md` is a short three-chapter manuscript to import.

To use ElevenLabs, set `ELEVENLABS_API_KEY` for both the API and the worker. To try the ElevenLabs path without a key, run the fake API and point the app at it:

```bash
.venv/bin/python -m studio.testing.fake_elevenlabs --port 8790 --failure-rate 0.1
ELEVENLABS_API_KEY=test-key STUDIO_ELEVENLABS_BASE_URL=http://127.0.0.1:8790 .venv/bin/studio-worker
```

For a production-style setup, `npm run build` and start the API with `STUDIO_WEB_DIST=web/dist` to serve the UI from the same origin.

## Tests

```bash
cd backend
ruff check . && mypy src tests
pytest -q                     # 102 tests; needs PostgreSQL, Redis and ffmpeg

cd ../web
npm run typecheck && npm test # 27 tests
```

Integration tests create their own database (`audiobook_studio_test`, override with `STUDIO_TEST_DATABASE_URL`) and use Redis db 13 (`STUDIO_TEST_REDIS_URL`) with a random key prefix per test. They cover:

- a worker process killed with `SIGKILL` mid-job, after which another worker finishes without re-synthesizing anything that was already cached
- retries of transient provider failures that only redo the failed segments
- a job lost by Redis that is recovered from PostgreSQL
- concurrent submits that create exactly one job
- the SSE stream over a real socket
- the Alembic migrations against the models

`bench/render_book.py` produces the numbers above, and `scripts/capture_preview.py` records `docs/preview.gif` from the running app.

## Layout

```
backend/src/studio/domain/    manuscript parsing, sentence splitting, segmentation, pronunciation, voice settings, cache keys
backend/src/studio/tts/       provider port, ElevenLabs adapter, offline providers
backend/src/studio/services/  project edits and re-keying, audio cache, synthesis, job submission
backend/src/studio/jobs/      Redis queue, worker, job context, synthesize and export handlers, event bus
backend/src/studio/render/    stitching, loudness normalization, MP3/AAC/M4B output
backend/src/studio/api/       FastAPI routes, schemas, read queries, SSE
backend/src/studio/testing/   fake ElevenLabs server, synthetic books, in-thread HTTP server
backend/migrations/           Alembic
web/src/                      React UI
bench/                        export benchmark and its results
scripts/                      preview capture
examples/                     sample manuscript
```

## Limitations

- The provider is chosen per deployment, not per project. A project created offline keeps offline voice ids if you later add an ElevenLabs key.
- Segment text is edited in place; an edit that grows a segment past the budget is not re-split.
- Pronunciation rules are plain alias replacement. ElevenLabs' own pronunciation dictionaries (IPA/CMU phonemes) are not used.
- Audio in the content store is never garbage-collected.

## License

MIT
