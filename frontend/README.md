# Frontend (React + Vite) - owned by the frontend teammate

The code here is a **placeholder** (`src/App.jsx`) so the containers can start. Replace it.

| Keep this | Why |
|---|---|
| `package.json` with `npm run build` producing `dist/` | The Docker image runs `npm ci` then `npm run build` and serves `dist/` with nginx |
| Commit `package-lock.json` | `npm ci` requires it. After adding a library run `npm install` and commit the updated lock file |
| Call the API with relative URLs under `/api/...` | nginx forwards `/api/*` to the backend, so no CORS setup and no hard-coded backend address |

How to call the backend: `fetch("/api/<route>")`, or use `API_BASE_URL` as in `src/App.jsx`.

Using yarn or pnpm instead of npm? Update the two install lines in `docker/frontend.Dockerfile`.

Develop with hot reload (backend and database in Docker): see "Working on one component" in the root README.
