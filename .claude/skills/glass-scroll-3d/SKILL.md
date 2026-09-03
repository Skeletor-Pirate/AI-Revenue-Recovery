---
name: glass-scroll-3d
description: >
  Build a premium scroll-driven React frontend that combines three techniques:
  scroll-as-timeline page choreography (scroll-craft), real-time 3D / WebGL
  scenes driven by React Three Fiber, and Apple-style liquid-glass UI chrome
  (navs, cards, HUD panels) that refracts the 3D behind it. Use for
  "3D scroll site in React", "react-three-fiber landing page", "glassmorphic
  scroll experience", "WebGL hero with scrollytelling", "liquid glass over a
  3D scene", or any request that wants a marketing / product page where a
  Three.js canvas, scroll animation, and frosted-glass panels work together.
allowed-tools: Bash, Read, Write, Edit, Glob, Grep, AskUserQuestion, WebFetch
---

# glass-scroll-3d

A composite frontend skill. It does not replace its three parts — it sequences
them and owns the seams between them.

| Layer | Owned by | This skill adds |
|---|---|---|
| Page grammar, feeling curve, scroll score, verification | **`scroll-craft`** skill | when to swap its video `scrub` device for an R3F scene |
| 3D / WebGL scene, camera, materials, scroll-linked animation | **`references/react-three-fiber.md`** here | the R3F ↔ scroll-progress binding |
| Nav / cards / HUD that read as glass | **`liquid-glass`** skill | using R3F canvas as the refracted backdrop, DOM glass on top |

## Order of work

1. **Run the `build-workflow` skill first** (its Step 1 is the Razorpay
   source-check) if this page is for the Buildathon
   submission (dashboard, landing, pitch site) — match Razorpay's product
   language and visual tone before choosing an aesthetic.
2. **Invoke the `scroll-craft` skill and do its Step 0 interview verbatim.** Do
   not skip it. The grammar, journey, feeling curve, and signature move all come
   from there. The only change: when a beat's device would be a video `scrub`,
   consider an R3F scene scrubbed by the same scroll progress instead (see
   below).
3. **Decide the render split.** Not every page needs a persistent canvas. Pick
   one:
   - **Single fixed canvas** behind the whole page (`position: fixed`), scene
     state driven by global scroll progress. Best for one continuous 3D world.
   - **Per-section canvases** mounted only while their section is near the
     viewport (`<Canvas>` inside a pinned section, unmounted otherwise). Best
     for distinct scenes. Cheaper on the GPU.
4. **Build the R3F scene(s)** per `references/react-three-fiber.md`.
5. **Add the liquid-glass DOM chrome** per the `liquid-glass` skill. The glass
   panels sit in normal DOM stacking above the canvas; `backdrop-filter`
   refracts whatever is painted behind — including a WebGL canvas — so the 3D
   scene shows through the glass rim for free.
6. **Verify with `scroll-craft` Step 5** (screenshot the scroll). Additionally
   check: canvas actually renders in the headless shot, no WebGL context-lost,
   framerate holds on the mobile profile, and `prefers-reduced-motion` freezes
   the scene on a good frame.

## The scroll ↔ 3D binding (the seam this skill owns)

`scroll-craft`'s engine exposes normalized scroll progress. React Three Fiber
animates from `useFrame`. Bind them, never fight them:

- Read scroll progress once per frame inside `useFrame` (via a ref updated by a
  scroll listener or drei's `useScroll`), lerp scene values toward it. Do not
  drive Three.js state from React re-renders on scroll — it will jank.
- If using `@react-three/drei`'s `<ScrollControls>`, let drei own the scroll
  container and read `useScroll().offset`; then `scroll-craft`'s DOM engine is
  not the scroller and you theme with its tokens only.
- If `scroll-craft`'s engine owns the scroll, expose its `--sc-p` / progress to
  R3F through a shared ref or a tiny store (Zustand / `valtio`), and keep
  `<Canvas>` outside React's scroll re-render path.
- One source of truth for scroll. Two scrollers = two scrollbars = broken.

## Liquid glass over WebGL — specifics

- The glass elements are plain DOM. Follow the `liquid-glass` skill's CSS recipe
  (border-radius, tint gradient, inset highlights, drop shadow) and call
  `liquidGlass(ref.current)` in a `useEffect`, `destroy()` on unmount.
- Refraction (Chromium) samples the composited page behind the element, so a
  fixed `<Canvas>` behind the glass refracts correctly. Safari/Firefox fall
  back to frosted blur automatically — the 3D still shows through, just without
  the rim bulge. Never make the refraction carry meaning.
- Keep glass panels under ~800px per side (GPU cost of the SVG filter stacks on
  top of the WebGL cost). Use glass for navs, stat cards, a HUD, a CTA — not
  full-bleed sections.
- If text over glass over a busy 3D scene smears: raise the glass `blur`
  option, raise the tint gradient alpha, or drop a local scrim behind the text
  — not an opaque panel.

## Stack

- React + Vite (or the project's existing React setup).
- `three`, `@react-three/fiber`, `@react-three/drei`. Add `@react-three/postprocessing`
  only if a beat needs bloom/DOF and the mobile profile still holds.
- `scroll-craft`'s `engine/scrollcraft.js` + `.css` copied into the build,
  unedited.
- `liquid-glass/liquid-glass.js` copied into the build, unedited.
- Optional tiny store for scroll progress: `zustand`.

## Hard rules (in addition to scroll-craft's)

| Never | Instead |
|---|---|
| A `<Canvas>` that re-renders on every scroll tick via React state | Drive the scene in `useFrame` from a ref; keep Canvas out of the scroll re-render path |
| Two scroll containers (drei ScrollControls + scroll-craft engine both scrolling) | Pick one scroller; the other reads its progress |
| Persistent full-screen canvas when only two sections are 3D | Per-section canvases, mounted near-viewport only |
| Shipping without a `prefers-reduced-motion` path for the 3D scene | Freeze the scene on a composed frame; disable `useFrame` animation |
| Liquid glass panels larger than ~800px/side over WebGL | Glass for chrome only; sections stay plain |
| No mobile GPU budget check | Verify framerate on the 390×844 profile in scroll-craft Step 5 |
| Baking UI text into the Three.js scene as a texture/mesh | Real DOM text in the glass layer above the canvas |

## Output

Whatever `scroll-craft`'s Output section asks for, plus: the render split chosen
and why, the scroll↔3D binding mechanism, which beats are R3F scenes vs video
vs DOM, and the mobile framerate you measured.
