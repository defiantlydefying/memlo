# Memlo website

The public product pages and trust center at [getmemlo.com](https://getmemlo.com).
This is separate from the Python Discord bot; see the [project README](../README.md)
and [bot setup guide](../SETUP.md) for those instructions.

## Develop locally

Use Node.js 22.13 or newer and npm:

```sh
cd policy-site
npm ci
npm run dev
```

Page content lives under `app/`, public assets under `public/`, and hosting
configuration in `.openai/hosting.json`. The site uses React and Vinext on
Cloudflare Workers. Preserve its existing Site identity when deploying updates.
The interactive Discord preview is a browser-only simulation, not a live bot.

## Verify changes

```sh
npm run audit
npm run typecheck
npm test
```

Tests build the site and check rendered product, trust, privacy, and terms pages.

## Dependency overrides

The checked-in lockfile and targeted npm overrides patch transitive dependencies
still pinned by upstream tools: esbuild under `@esbuild-kit/core-utils`, image-size
under `vinext`, and Undici under `miniflare`. Remove an override only when upstream
resolves to a patched version without it, then regenerate the lockfile and rerun
the checks. Use `npx drizzle-kit check` when changing migration tooling.
