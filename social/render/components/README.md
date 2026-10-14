# SOC-2 game-card trajectory source

`RatingTrajectoryChart` on the site is hand-rolled React/SVG (no chart library):

- Source: `webapp/site/src/components/GameDetail/RatingTrajectoryChart.tsx`
- Commit: `98124ba54cc09d6d26c54f29c3501ab45d8f1db7`
- Geometry: `webapp/site/src/lib/game-detail/geometry.ts`
- Tokens: `webapp/site/src/styles/tokens.css` (imported read-only by the renderer)
- Format: `webapp/site/src/lib/formatting/numbers.ts` (mirrored in `src/format.mjs`)

The Next.js component uses CSS modules and `@/` aliases, so it does not run
outside the app. SOC-2 ports the same encoding into
`social/render/src/geometry.mjs` + `charts.mjs` `renderGamecard` (flat ±1 SD
bands, solid home / dashed away, open circle at the current week, identical
offense/defense helper sentences). Do not edit anything under `webapp/`.
