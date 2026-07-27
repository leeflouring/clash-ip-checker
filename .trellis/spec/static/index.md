# Static Web Layer

## Pre-Development Checklist

- Read `result-workspace-contract.md` before changing the Docker page, workspace
  API calls, result table, reconnect behavior, exports, or Clash deep links.
- Verify API field names against `main.py`; do not infer a second client-side
  model.
- Keep the page self-contained. Use semantic HTML, native form controls, CSS,
  and browser APIs before adding dependencies.

## Quality Check

- Run `node --check static/js/app.js`.
- Run the workspace API tests and `git diff --check`.
- Load `/ipcheck` at desktop and 390px mobile widths.
- Confirm `/static/css/style.css` and `/static/js/app.js` return 200 and the
  browser console has no errors.
- Inspect web storage and rendered text: only the opaque job ID may persist;
  subscription URLs must not.
