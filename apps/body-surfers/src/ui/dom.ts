// Tiny DOM helpers + cached bindings. Every binding remembers the last value it wrote, so
// render() can be called every frame and only touches the DOM when something changed.
import { reducedMotion } from '../shared/motion';

export type Child = Node | string | null | undefined | false;

export function h<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  cls?: string | null,
  ...children: Child[]
): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  for (const c of children) {
    if (c === null || c === undefined || c === false) continue;
    e.append(typeof c === 'string' ? document.createTextNode(c) : c);
  }
  return e;
}

/** Parse trusted, static markup (icons / pictograms defined in this module). */
export function svg(markup: string): Element {
  const t = document.createElement('template');
  t.innerHTML = markup.trim();
  const first = t.content.firstElementChild;
  if (!first) throw new Error('svg(): empty markup');
  return first;
}

/** Wraps static markup in a span with a class. */
export function icon(markup: string, cls: string): HTMLSpanElement {
  const s = h('span', cls);
  s.append(svg(markup));
  return s;
}

/** Cached textContent. */
export class Text {
  private v: string | null = null;
  constructor(readonly el: Element) {}
  set(s: string): boolean {
    if (s === this.v) return false;
    this.v = s;
    this.el.textContent = s;
    return true;
  }
}

/** Cached numeric textContent; formats only when the number changes. */
export class NumText {
  private v = Number.NaN;
  constructor(
    readonly el: Element,
    private readonly fmt: (n: number) => string = String,
  ) {}
  set(n: number): boolean {
    if (n === this.v) return false;
    this.v = n;
    this.el.textContent = this.fmt(n);
    return true;
  }
}

/** Cached class toggle. */
export class Flag {
  private v: boolean | null = null;
  constructor(
    readonly el: Element,
    readonly cls: string,
  ) {}
  set(on: boolean): boolean {
    if (on === this.v) return false;
    this.v = on;
    this.el.classList.toggle(this.cls, on);
    return true;
  }
  get value(): boolean {
    return this.v === true;
  }
}

/** Cached visibility (adds `.is-hidden`, which is `display: none`). */
export class Show {
  private v: boolean | null = null;
  constructor(readonly el: Element) {}
  set(visible: boolean): boolean {
    if (visible === this.v) return false;
    this.v = visible;
    this.el.classList.toggle('is-hidden', !visible);
    return true;
  }
  get visible(): boolean {
    return this.v === true;
  }
}

/** Cached attribute / data-attribute. */
export class Attr {
  private v: string | null = null;
  constructor(
    readonly el: Element,
    readonly name: string,
  ) {}
  set(s: string): boolean {
    if (s === this.v) return false;
    this.v = s;
    this.el.setAttribute(this.name, s);
    return true;
  }
}

/** Cached numeric CSS custom property, quantized to avoid needless style recalcs. */
export class CssNum {
  private v = Number.NaN;
  constructor(
    readonly el: HTMLElement,
    readonly name: string,
    private readonly quantum = 0.002,
  ) {}
  set(n: number): boolean {
    const q = Math.round(n / this.quantum) * this.quantum;
    if (q === this.v) return false;
    this.v = q;
    this.el.style.setProperty(this.name, q.toFixed(4));
    return true;
  }
}

/** Cached string CSS custom property (colors etc.). */
export class CssStr {
  private v: string | null = null;
  constructor(
    readonly el: HTMLElement,
    readonly name: string,
  ) {}
  set(s: string): boolean {
    if (s === this.v) return false;
    this.v = s;
    this.el.style.setProperty(this.name, s);
    return true;
  }
}

/** Cached <img> src (data URLs can be large: compare by identity first). */
export class ImgSrc {
  private v: string | null = null;
  constructor(readonly el: HTMLImageElement) {}
  set(src: string): boolean {
    if (src === this.v) return false;
    this.v = src;
    this.el.src = src;
    return true;
  }
}

/** Keeps buttons from stealing keyboard focus (so Space/Enter keep driving the game). */
export function noFocus(el: HTMLElement): void {
  el.addEventListener('mousedown', (e) => e.preventDefault());
}

/** A <button type=button> that never keeps focus. */
export function button(cls: string, ...children: Child[]): HTMLButtonElement {
  const b = h('button', cls, ...children);
  b.type = 'button';
  noFocus(b);
  return b;
}

/** Restart a one-shot Web Animation on an element (cheap: only called on value changes). */
export function pop(el: Element, keyframes: Keyframe[], duration: number, easing = 'cubic-bezier(.2,1.6,.4,1)'): void {
  if (typeof (el as HTMLElement).animate !== 'function' || reducedMotion()) return;
  for (const a of el.getAnimations()) if (a.id === 'bs-pop') a.cancel();
  const anim = el.animate(keyframes, { duration, easing });
  anim.id = 'bs-pop';
}
