# Contributing to Analysarr

Thanks for your interest! Bug reports, ideas and pull requests are welcome.

## Before you start

- **Bugs:** open an issue with the bug report template.
- **Features:** open a feature request first, so we can agree on the approach before you spend time on code.
- **Security issues:** never in public — see [SECURITY.md](SECURITY.md).

## Branches

- `main` holds released code. It is protected and only receives merges from `dev`.
- `dev` is the integration branch: **open your pull requests against `dev`**.

## Development setup

Requirements: Python 3.12, Node.js 22.

**Backend** (FastAPI):

```bash
cd backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
DATABASE_PATH=./data/analysarr.db uvicorn app.main:app --reload --port 8000
```

**Frontend** (React + Vite), in another terminal:

```bash
cd frontend
npm ci
npm run dev
```

Open `http://localhost:5173`. Vite forwards `/api` to the backend on port 8000.

## Before opening a pull request

```bash
cd frontend && npm run lint && npm run build
cd backend && python -c "from app.main import app"
```

The CI runs the same checks, plus a dependency audit and a Docker build.

## Conventions

- **Commits:** [Conventional Commits](https://www.conventionalcommits.org/) — `feat(scope): …`, `fix(scope): …`, `docs: …`, `ci: …`. Release notes are generated from them.
- **Code style:** follow the surrounding code. Comments explain *why*, not *what*.
- **Interface text:** every string goes through the i18n dictionaries — add each new key to **both** `frontend/src/i18n/fr.ts` and `frontend/src/i18n/en.ts` (the build fails otherwise). Never hardcode "Emby": use the `{server}` variable so Jellyfin users see the right name.
- **Destructive actions:** anything that deletes or modifies files must show a preview and require an explicit confirmation.
- **Security:**
  - never send API keys or passwords back to the browser;
  - validate every input on the backend;
  - never render untrusted content as HTML (`dangerouslySetInnerHTML` is not allowed);
  - new outbound connections must only target services configured by the administrator.

## License

By contributing, you agree that your contributions are licensed under the [GNU AGPL-3.0](LICENSE).
