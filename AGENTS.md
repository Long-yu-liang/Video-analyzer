# Repository Guidelines

## Project Structure & Module Organization

- `demo/app.py`: Python HTTP server, media preprocessing, model requests, validation, and result storage. `demo/start.py` is an alternative launcher.
- `demo/web/index.html`: browser interface with inline CSS and JavaScript; no frontend build step.
- `demo/tests/`: Python tests and the Node.js UI smoke test.
- `prompts/`: historical `vN.txt` prompts and staged prompts such as `v14/events.txt` and `v13/opportunities.txt`.
- `mp4-example/`: sample videos. `results/<model>/` stores original inference records. `day1/` and `day2/` contain evaluations, annotations, and reports.

The two-stage workflow records observable events, lets users review them, then analyzes selected frames for participation opportunities. Preserve the legacy single-stage workflow when changing shared code.

## Build, Test, and Development Commands

Run commands from the repository root. Install Python 3.10+ and FFmpeg/ffprobe on `PATH`; Node.js enables UI checks. The backend uses Python's standard library.

- `python demo/app.py`: start the local server at `http://127.0.0.1:8765` by default.
- `python -m unittest discover -s demo/tests -v`: run local tests, including FFmpeg checks and the UI smoke test when Node.js is available.
- `node demo/tests/ui_smoke.js`: run the browser-script fixture directly without calling a model.

## Coding Style & Naming Conventions

Use four-space indentation and `snake_case` functions in Python; use two-space indentation and `camelCase` for new JavaScript. Follow surrounding code and avoid unrelated formatting changes. No formatter or linter configuration is established.

Keep files UTF-8 to preserve Chinese prompts and sample names. Add new prompt versions by default, retaining earlier prompts and raw responses. Preserve actual request parameters, timestamps, video hashes, and usage when changing inference records.

## Testing Guidelines

Use `unittest`, `unittest.mock`, `test_*.py` files, and `test_*` methods. Cover changed behavior: timestamp mapping, invalid or empty output, truncation, stage linkage, and click-to-seek interactions. Keep model responses mocked in local tests. There is no configured coverage percentage target.

Real model experiments incur charges. Record failures alongside successes, and distinguish schema validity from factual accuracy. Compare results under matching model, media, input mode, and sampling settings.

## Commit & Pull Request Guidelines

Existing history uses concise English summaries, including imperative messages; no Conventional Commits prefix is established. Keep commits focused. PR descriptions should explain the behavior change, validation, relevant issue or evaluation report, and remaining limitations. Include screenshots for visible UI changes.

## Security & Configuration

Copy `.env.example` to `.env`, configure `BEEROUTE_MODELS` and credentials, then restart the server. Never commit or print API keys; `.env` is ignored. Preserve original videos, source attribution, and historical experiment data.
