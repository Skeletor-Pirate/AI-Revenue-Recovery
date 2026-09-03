# React Three Fiber — working reference

Source of truth: https://r3f.docs.pmnd.rs/ · drei: https://drei.docs.pmnd.rs/
Fetch those with WebFetch if an API detail is uncertain — do not guess signatures.

R3F is a React renderer for three.js. JSX elements map to three objects:
`<mesh>` → `new THREE.Mesh()`, `<meshStandardMaterial>` → `new THREE.MeshStandardMaterial()`.
`args` is the constructor argument array. Props set properties; dashed props set
nested props (`position-x={1}`).

## Versions

- React 19 + `@react-three/fiber` v9, or React 18 + v8. Match the project's React.
- `three` latest, `@react-three/drei` latest, `@react-three/postprocessing` only if needed.

```bash
npm i three @react-three/fiber @react-three/drei
npm i -D @types/three   # TS projects
```

## Minimal scene

```jsx
import { Canvas, useFrame } from '@react-three/fiber'
import { useRef } from 'react'

function Spinner() {
  const ref = useRef()
  useFrame((state, delta) => { ref.current.rotation.y += delta * 0.4 })
  return (
    <mesh ref={ref}>
      <icosahedronGeometry args={[1, 0]} />
      <meshStandardMaterial color="#ff5a3d" flatShading />
    </mesh>
  )
}

export default function Scene() {
  return (
    <Canvas camera={{ position: [0, 0, 4], fov: 45 }} dpr={[1, 2]}>
      <ambientLight intensity={0.4} />
      <directionalLight position={[3, 5, 2]} intensity={1.2} />
      <Spinner />
    </Canvas>
  )
}
```

## Core rules

- **Never `setState` inside `useFrame`.** Mutate refs / three objects directly.
  React re-renders are for structural change only.
- **`useFrame((state, delta) => …)`** runs every frame. `delta` is seconds since
  last frame — multiply all motion by it so speed is framerate-independent.
- **`state`** holds `camera`, `scene`, `gl`, `clock`, `pointer` (`{x,y}` in
  NDC -1..1), `viewport`, `size`.
- **`dpr={[1, 2]}`** caps devicePixelRatio — uncapped retina kills mobile.
- **Dispose is automatic** when a component unmounts, *if* geometry/material are
  declared as JSX children (not created with `useMemo` and reused). If you
  `useMemo(() => new THREE.X())`, dispose it in a cleanup.
- **`frameloop="demand"`** on `<Canvas>` renders only when something invalidates
  (call `invalidate()`), for static scenes — big battery win. For scroll-linked
  animation use the default `"always"` while visible.

## Binding to scroll progress

Two options — pick one per the SKILL.md render split.

### A. drei owns the scroll (`<ScrollControls>`)

```jsx
import { ScrollControls, useScroll, Scroll } from '@react-three/drei'

<Canvas>
  <ScrollControls pages={4} damping={0.2}>
    <SceneContents />
    <Scroll html>{/* DOM that scrolls with the canvas */}</Scroll>
  </ScrollControls>
</Canvas>

function SceneContents() {
  const scroll = useScroll()          // scroll.offset: 0..1, scroll.range(a,b)
  const ref = useRef()
  useFrame(() => {
    const p = scroll.offset
    ref.current.position.y = THREE.MathUtils.lerp(2, -2, p)
    ref.current.rotation.z = p * Math.PI
  })
  return <mesh ref={ref}>…</mesh>
}
```

Use this when the 3D world is the whole page. Then the scroll-craft engine is
NOT the scroller — use only its CSS tokens for theming DOM inside `<Scroll html>`.

### B. scroll-craft's engine owns the scroll

Canvas is `position: fixed` behind the page. Share progress via a ref or store.

```jsx
// store.js
import { create } from 'zustand'
export const useScrollP = create(() => ({ p: 0 }))

// somewhere in the DOM app, subscribed to scroll-craft's progress:
useScrollP.setState({ p: currentProgress })   // 0..1, from window scroll or --sc-p

// in the scene:
function SceneContents() {
  const ref = useRef()
  useFrame(() => {
    const p = useScrollP.getState().p        // read, don't subscribe
    ref.current.rotation.y = p * Math.PI * 2
  })
  return <mesh ref={ref}>…</mesh>
}
```

`getState()` in `useFrame` reads without causing a React subscription/re-render.

To read scroll-craft's `--sc-p` CSS var:
`getComputedStyle(document.documentElement).getPropertyValue('--sc-p')` once per
frame is fine, or have the scroll listener push into the store.

## Per-section canvas (mount near viewport)

```jsx
function Section3D() {
  const [inView, setInView] = useState(false)
  const wrap = useRef()
  useEffect(() => {
    const io = new IntersectionObserver(
      ([e]) => setInView(e.isIntersecting),
      { rootMargin: '50% 0px' }               // mount a bit before it enters
    )
    io.observe(wrap.current)
    return () => io.disconnect()
  }, [])
  return (
    <section ref={wrap} data-sc-act style={{ height: '200vh' }}>
      {inView && <Canvas>…</Canvas>}
    </section>
  )
}
```

## Common drei helpers

| Helper | Use |
|---|---|
| `<OrbitControls />` | dev only — remove for a scroll site |
| `<Environment preset="city" />` | image-based lighting; instant realistic materials |
| `<Float>` | idle bob/rotation for hero objects |
| `useGLTF('/model.glb')` + `<primitive object={…} />` | load a model; call `useGLTF.preload` |
| `<MeshTransmissionMaterial>` | real glass *material* for 3D objects (distinct from the DOM liquid-glass) |
| `<PerspectiveCamera makeDefault>` | declarative camera you can animate |
| `<Preload all />` | force-compile assets so first scroll frame isn't a stall |
| `<AdaptiveDpr pixelated />` | drop resolution during movement, restore when idle |

## Performance checklist (verify on the mobile profile)

- `dpr={[1, 2]}`, and `<AdaptiveDpr>` if the scene is heavy.
- Reuse geometries/materials; instance repeated meshes (`<Instances>`).
- `<Environment>` / HDRI over many real lights. Shadows are expensive — one
  shadow-casting light max, tight `shadow-camera` bounds, or bake them.
- Lazy-load `.glb` and compress with draco/meshopt. `useGLTF.preload()` the hero.
- Postprocessing (bloom/DOF) roughly halves mobile framerate — measure before
  keeping it.
- Watch for `THREE.WebGLRenderer: Context Lost` in the console during Step 5.

## prefers-reduced-motion

```jsx
const reduced = useReducedMotion()  // from framer-motion, or a matchMedia hook
useFrame(() => { if (reduced) return; /* animate */ })
```

Compose one good static frame (set positions/rotations to a chosen progress
value) and render it once. The page must still tell its story with the scene
frozen.

## Headless render note (scroll-craft Step 5)

Headless Chrome renders WebGL via SwiftShader (slow but works). If the shot is
blank: ensure the canvas has non-zero size before the screenshot, add a short
settle wait after scroll, and confirm `frameloop` isn't `"demand"` without an
`invalidate()`. A real GPU check still needs a real device.
