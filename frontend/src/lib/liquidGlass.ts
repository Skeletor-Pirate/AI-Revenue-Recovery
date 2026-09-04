/*!
 * liquidGlass.ts — Apple-style liquid glass refraction engine
 * Based on .claude/skills/frontend/liquid-glass/liquid-glass.js
 */

import { useEffect, type RefObject } from 'react'

export interface LiquidGlassOptions {
  scale?: number
  chroma?: number
  border?: number
  mapBlur?: number
  blur?: number
  saturate?: number
  radius?: number | null
  fallbackBlur?: number
}

export interface LiquidGlassInstance {
  supported: boolean
  refresh: () => void
  destroy: () => void
}

const SVG_NS = 'http://www.w3.org/2000/svg'
let uid = 0
let svgDefs: SVGDefsElement | null = null

// Chromium can apply SVG filters via backdrop-filter; Safari and Firefox
// silently no-op, so they get the frosted fallback instead.
const isSupported = (): boolean => {
  if (typeof window === 'undefined') return false
  const ua = navigator.userAgent
  const isSafari = /Safari/.test(ua) && !/Chrome|Chromium|Edg/.test(ua)
  const isFirefox = /Firefox/.test(ua)
  if (isSafari || isFirefox) return false
  if (typeof CSS === 'undefined' || !CSS.supports('backdrop-filter', 'url(#lg)')) return false
  try {
    const c = document.createElement('canvas')
    c.width = c.height = 4
    c.getContext('2d')?.getImageData(0, 0, 1, 1)
    return true
  } catch {
    return false
  }
}

function ensureDefs(): SVGDefsElement {
  if (svgDefs && svgDefs.isConnected) return svgDefs
  const svg = document.createElementNS(SVG_NS, 'svg')
  svg.setAttribute('width', '0')
  svg.setAttribute('height', '0')
  svg.setAttribute('aria-hidden', 'true')
  svg.style.position = 'absolute'
  svg.style.pointerEvents = 'none'
  svg.style.overflow = 'hidden'
  svgDefs = document.createElementNS(SVG_NS, 'defs')
  svg.appendChild(svgDefs)
  document.body.appendChild(svg)
  return svgDefs
}

function makeMap(w: number, h: number, radius: number, border: number, mapBlur: number): string {
  const canvas = document.createElement('canvas')
  canvas.width = w
  canvas.height = h
  const ctx = canvas.getContext('2d')
  if (!ctx) return ''

  const gx = ctx.createLinearGradient(0, 0, w, 0)
  gx.addColorStop(0, 'rgb(0,0,0)')
  gx.addColorStop(1, 'rgb(255,0,0)')
  ctx.fillStyle = gx
  ctx.fillRect(0, 0, w, h)

  const gy = ctx.createLinearGradient(0, 0, 0, h)
  gy.addColorStop(0, 'rgb(0,0,0)')
  gy.addColorStop(1, 'rgb(0,0,255)')
  ctx.globalCompositeOperation = 'difference'
  ctx.fillStyle = gy
  ctx.fillRect(0, 0, w, h)

  ctx.globalCompositeOperation = 'source-over'
  const inset = border * Math.min(w, h)
  ctx.filter = `blur(${mapBlur}px)`
  ctx.fillStyle = 'rgba(128,128,128,0.93)'
  ctx.beginPath()
  ctx.roundRect(inset, inset, w - inset * 2, h - inset * 2, Math.max(radius - inset, 2))
  ctx.fill()
  ctx.filter = 'none'
  return canvas.toDataURL()
}

function buildFilter(id: string, scales: number[]) {
  const filter = document.createElementNS(SVG_NS, 'filter')
  filter.setAttribute('id', id)
  filter.setAttribute('x', '0')
  filter.setAttribute('y', '0')
  filter.setAttribute('width', '100%')
  filter.setAttribute('height', '100%')
  filter.setAttribute('color-interpolation-filters', 'sRGB')

  const feImage = document.createElementNS(SVG_NS, 'feImage')
  feImage.setAttribute('x', '0')
  feImage.setAttribute('y', '0')
  feImage.setAttribute('result', 'map')
  feImage.setAttribute('preserveAspectRatio', 'none')
  filter.appendChild(feImage)

  const keep = [
    '1 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 1 0',
    '0 0 0 0 0  0 1 0 0 0  0 0 0 0 0  0 0 0 1 0',
    '0 0 0 0 0  0 0 0 0 0  0 0 1 0 0  0 0 0 1 0',
  ]
  const channels: string[] = []
  for (let i = 0; i < 3; i++) {
    const disp = document.createElementNS(SVG_NS, 'feDisplacementMap')
    disp.setAttribute('in', 'SourceGraphic')
    disp.setAttribute('in2', 'map')
    disp.setAttribute('scale', String(scales[i]))
    disp.setAttribute('xChannelSelector', 'R')
    disp.setAttribute('yChannelSelector', 'B')
    disp.setAttribute('result', 'd' + i)
    filter.appendChild(disp)

    const cm = document.createElementNS(SVG_NS, 'feColorMatrix')
    cm.setAttribute('in', 'd' + i)
    cm.setAttribute('type', 'matrix')
    cm.setAttribute('values', keep[i])
    cm.setAttribute('result', 'c' + i)
    filter.appendChild(cm)
    channels.push('c' + i)
  }

  const blend1 = document.createElementNS(SVG_NS, 'feBlend')
  blend1.setAttribute('in', channels[0])
  blend1.setAttribute('in2', channels[1])
  blend1.setAttribute('mode', 'screen')
  blend1.setAttribute('result', 'c01')
  filter.appendChild(blend1)

  const blend2 = document.createElementNS(SVG_NS, 'feBlend')
  blend2.setAttribute('in', 'c01')
  blend2.setAttribute('in2', channels[2])
  blend2.setAttribute('mode', 'screen')
  filter.appendChild(blend2)

  ensureDefs().appendChild(filter)
  return { filter, feImage }
}

function resolveRadius(el: HTMLElement, w: number, h: number, override?: number | null): number {
  if (override != null) return override
  const raw = getComputedStyle(el).borderTopLeftRadius || '0px'
  const v = parseFloat(raw) || 0
  return raw.trim().endsWith('%') ? (v / 100) * Math.min(w, h) : v
}

export function liquidGlass(el: HTMLElement, opts?: LiquidGlassOptions): LiquidGlassInstance {
  const o = Object.assign(
    {
      scale: -112,
      chroma: 6,
      border: 0.07,
      mapBlur: 12,
      blur: 4,
      saturate: 1.5,
      radius: null,
      fallbackBlur: 20,
    },
    opts,
  )

  const supported = isSupported()

  if (!supported) {
    const frosted = `blur(${o.fallbackBlur}px) saturate(${o.saturate})`
    el.style.backdropFilter = frosted
    ;(el.style as unknown as { webkitBackdropFilter: string }).webkitBackdropFilter = frosted
    el.classList.add('lg-fallback')
    return {
      supported: false,
      refresh: () => {},
      destroy: () => {
        el.style.backdropFilter = ''
        ;(el.style as unknown as { webkitBackdropFilter: string }).webkitBackdropFilter = ''
        el.classList.remove('lg-fallback')
      },
    }
  }

  const id = 'lg-filter-' + ++uid
  const scales = [o.scale, o.scale + o.chroma, o.scale + 2 * o.chroma]
  const parts = buildFilter(id, scales)

  function refresh() {
    const w = el.offsetWidth
    const h = el.offsetHeight
    if (!w || !h) return
    const radius = resolveRadius(el, w, h, o.radius)
    const mapData = makeMap(w, h, radius, o.border, o.mapBlur)
    if (mapData) {
      parts.feImage.setAttribute('href', mapData)
      parts.feImage.setAttribute('width', String(w))
      parts.feImage.setAttribute('height', String(h))
    }
  }

  refresh()
  el.style.backdropFilter = `url(#${id}) blur(${o.blur}px) saturate(${o.saturate})`
  ;(el.style as unknown as { webkitBackdropFilter: string }).webkitBackdropFilter =
    `blur(${o.fallbackBlur}px) saturate(${o.saturate})`

  let timer: number | null = null
  const ro = new ResizeObserver(() => {
    if (timer) window.clearTimeout(timer)
    timer = window.setTimeout(refresh, 120)
  })
  ro.observe(el)

  return {
    supported: true,
    refresh,
    destroy: () => {
      ro.disconnect()
      if (timer) window.clearTimeout(timer)
      parts.filter.remove()
      el.style.backdropFilter = ''
      ;(el.style as unknown as { webkitBackdropFilter: string }).webkitBackdropFilter = ''
    },
  }
}

/**
 * React hook to bind the Liquid Glass refraction effect to any element ref.
 */
export function useLiquidGlass<T extends HTMLElement>(
  ref: RefObject<T | null>,
  options?: LiquidGlassOptions,
  active: boolean = true,
) {
  useEffect(() => {
    if (!active || !ref.current) return
    const instance = liquidGlass(ref.current, options)
    return () => instance.destroy()
  }, [ref, active, options])
}
