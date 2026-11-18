---
paths:
  - "**/*.js"
  - "**/*.mjs"
  - "**/*.cjs"
---

# JavaScript

<!-- Standard of bearclave/governance (standards/code/javascript.md). In other repositories this is a managed copy - change it in governance. -->

- Websites are static: no framework and no build step unless the site
  needs one. Tooling (`tools/*.mjs`) runs on the Node preinstalled on the
  runner, without packages.
- Azure Functions (`api/`): CommonJS or ESM consistently per app, the
  dependencies the function really needs, pinned in `package-lock.json`.
- `const` by default, `let` when reassigned, never `var`; strict equality;
  `async`/`await` over promise chains.
- No inline scripts or styles in pages - the Content Security Policy forbids
  them; the CSP check of the site fails on them.
- Input from a request is validated and length-limited before use; nothing
  from a request reaches HTML without escaping.
- Tests (`tools/test-*.cjs`) for every function behind a form or an API.
