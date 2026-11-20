# Web performance review — 2026-11-19

This was a report-only review. No application code was changed.

## Build

`NEXT_DIST_DIR=.next-copilot-perf npm run build` completed successfully with Next.js 16.4.6 and Turbopack. The temporary build directory was removed after measurement.

The build emitted 603 files totalling approximately 35.33 MB. This includes server artifacts, manifests, source maps, and static assets; it is not the download size of a page.

## Routes

The 15 top-level prerendered route HTML files measured between 13.2 KB and 16.5 KB:

| Route | HTML |
|---|---:|
| `/feed`, `/review` | 13.2 KB each |
| `/signin` | 13.5 KB |
| `/new` | 14.0 KB |
| `/alerts` | 14.4 KB |
| `/more` | 14.3 KB |
| `/queue` | 14.7 KB |
| `/listing` | 15.0 KB |
| `/campaign` | 15.1 KB |
| `/transcript` | 15.5 KB |
| `/profile` | 16.1 KB |
| `/` | 16.4 KB |
| `/explore` | 16.5 KB |
| `/_not-found` | 15.2 KB |
| `/_global-error` | 8.8 KB |

These are static shell sizes. Runtime API responses and video/audio media are not included.

## Client bundles and dependencies

The largest hashed client chunks were approximately 223.9 KB, 175.3 KB, and 110.0 KB, followed by 62.2 KB. The largest CSS chunk was 26.0 KB. The chunk names are build hashes, so a bundle analyzer would be needed to attribute them reliably to individual dependencies.

The package manifest is small: Next.js, React, Motion, SimpleWebAuthn, and the test toolchain. No unusually large image was found in `web/public`; the largest was `icon-512.png` at 9.0 KB. No bundled webfont files or `next/font` imports were found; the CSS uses system/font-family fallbacks.

## Risks and suggestions

- **Shared client JavaScript — medium:** the largest chunks are shared across the application, so every interactive route may pay a meaningful initial script cost. Add a bundle analyzer before changing dependencies, then split only components proven to be route-specific.
- **Media layout shift — low/medium:** several screens render remote `<video>` elements. Most have explicit dimensions or a fixed full-screen container, but media metadata and poster availability can still affect perceived loading. Keep poster URLs and reserved aspect-ratio boxes for list thumbnails.
- **Fixed mobile controls — low:** the bottom navigation, toasts, and full-screen feed use fixed positioning. The existing CSS accounts for safe-area insets and reserves bottom padding; verify any future copy changes at 375px width and in Arabic before merging.
- **No image/font issue found:** public raster assets are small, and fonts are not downloaded by the app.

No safe, trivial optimization was justified from this measurement alone. The next useful step is dependency attribution with a bundle analyzer, followed by targeted code splitting only if it demonstrates a measurable route-level benefit.
