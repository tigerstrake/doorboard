// Settings panel (backquote key): camera, model, quality, audio, tutorial, webcam overlay, mimic,
// live vision-tuning sliders, reset defaults, clear leaderboard with an inline confirm.
import type { Settings, UIModel, VisionTuning } from '../../shared/types';
import { DEFAULT_SETTINGS, DEFAULT_VISION_TUNING } from '../../shared/constants';
import { button, Flag, h, icon, Show, Text } from '../dom';
import { ICONS } from '../icons';
import { CLEAR_CONFIRM_MS } from '../config';
import type { Emit } from './common';

type NumKey = 'laneThreshold' | 'laneHysteresis' | 'jumpThreshold' | 'duckThreshold' | 'smoothing';

interface Slider {
  input: HTMLInputElement;
  value: Text;
  last: number;
  fmt: (v: number) => string;
}

interface Toggle {
  btn: HTMLButtonElement;
  on: Flag;
  label: Text;
}

interface Segmented {
  btns: Map<string, Flag>;
}

const VISION_SLIDERS: { key: NumKey; label: string; min: number; max: number; step: number; help: string }[] = [
  { key: 'laneThreshold', label: 'Lane step distance', min: 0.5, max: 1.5, step: 0.05, help: 'How far to step sideways (× shoulder width)' },
  { key: 'laneHysteresis', label: 'Lane stickiness', min: 0.1, max: 0.6, step: 0.05, help: 'How far back before leaving a side lane' },
  { key: 'jumpThreshold', label: 'Jump height', min: 0.15, max: 0.6, step: 0.01, help: 'Body rise that counts as a jump' },
  { key: 'duckThreshold', label: 'Squat depth', min: 0.25, max: 0.8, step: 0.01, help: 'Body drop that counts as a squat' },
  { key: 'smoothing', label: 'Smoothing', min: 0, max: 1, step: 0.05, help: 'Higher = steadier but laggier' },
];

export class SettingsPanel {
  readonly root: HTMLElement;
  private readonly show: Show;
  private settings: Settings = DEFAULT_SETTINGS;
  private readonly camSelect: HTMLSelectElement;
  private camKey = '';
  private readonly model: Segmented;
  private readonly quality: Segmented;
  private readonly mute: Toggle;
  private readonly tutorial: Toggle;
  private readonly webcam: Toggle;
  private readonly mimic: Toggle;
  private readonly mirror: Toggle;
  private readonly music: Slider;
  private readonly sfx: Slider;
  private readonly vision = new Map<NumKey, Slider>();
  private readonly clearAsk: Show;
  private readonly clearConfirm: Show;
  private confirmUntil = 0;
  private wasOpen = false;

  constructor(private readonly emit: Emit) {
    const close = button('bs-set__close', icon(ICONS.close, 'bs-set__closeicon'));
    close.title = 'Close (`)';
    close.addEventListener('click', () => emit({ type: 'settings-open', open: false }));

    this.camSelect = h('select', 'bs-set__select');
    this.camSelect.addEventListener('change', () => {
      const v = this.camSelect.value;
      this.change({ cameraId: v === '' ? null : v });
      this.camSelect.blur();
    });
    this.camSelect.addEventListener('keydown', (e) => e.stopPropagation());

    const modelSeg = this.segmented(['lite', 'full'], ['LITE (fast)', 'FULL (accurate)'], (v) => this.change({ model: v as Settings['model'] }));
    const qualitySeg = this.segmented(['low', 'high'], ['LOW', 'HIGH'], (v) => this.change({ quality: v as Settings['quality'] }));
    this.model = modelSeg.seg;
    this.quality = qualitySeg.seg;

    this.mute = this.toggle((on) => this.change({ muted: on }));
    this.tutorial = this.toggle((on) => this.change({ tutorial: on }));
    this.webcam = this.toggle((on) => this.change({ showWebcam: on }));
    this.mimic = this.toggle((on) => this.change({ mimic: on }));
    this.mirror = this.toggle((on) => this.changeVision({ mirror: on }));

    this.music = this.slider(0, 1, 0.05, (v) => this.change({ musicVolume: v }), pct);
    this.sfx = this.slider(0, 1, 0.05, (v) => this.change({ sfxVolume: v }), pct);

    const visionRows: HTMLElement[] = [];
    for (const d of VISION_SLIDERS) {
      const s = this.slider(d.min, d.max, d.step, (v) => this.changeVision({ [d.key]: v }), (v) => v.toFixed(2));
      this.vision.set(d.key, s);
      visionRows.push(row(d.label, sliderWrap(s), d.help));
    }

    const reset = button('bs-btn bs-btn--blue bs-btn--sm', 'RESET DEFAULTS');
    reset.addEventListener('click', () => {
      this.change({ ...DEFAULT_SETTINGS, cameraId: this.settings.cameraId, vision: { ...DEFAULT_VISION_TUNING } });
    });
    const clear = button('bs-btn bs-btn--red bs-btn--sm', 'CLEAR LEADERBOARD');
    const yes = button('bs-btn bs-btn--red bs-btn--sm', 'YES, CLEAR IT');
    const no = button('bs-btn bs-btn--white bs-btn--sm', 'CANCEL');
    const askEl = h('div', 'bs-set__clear', clear);
    const confirmEl = h('div', 'bs-set__clear bs-set__confirm', h('span', 'bs-set__confirmq', 'Delete all scores & photos?'), yes, no);
    clear.addEventListener('click', () => {
      this.confirmUntil = performance.now() + CLEAR_CONFIRM_MS;
      this.syncConfirm();
    });
    yes.addEventListener('click', () => {
      this.confirmUntil = 0;
      this.syncConfirm();
      emit({ type: 'clear-leaderboard' });
    });
    no.addEventListener('click', () => {
      this.confirmUntil = 0;
      this.syncConfirm();
    });
    this.clearAsk = new Show(askEl);
    this.clearConfirm = new Show(confirmEl);

    // Gesture tuning is for tinkerers: tucked into a collapsed "Advanced" section.
    const advanced = h('details', 'bs-set__adv');
    const summary = h('summary', 'bs-set__section bs-set__advsum', 'Advanced: gesture tuning');
    summary.addEventListener('keydown', (e) => e.stopPropagation());
    advanced.append(summary, h('div', 'bs-set__advgrid', ...visionRows));

    const panel = h(
      'div',
      'bs-set__panel bs-sticker',
      h('header', 'bs-set__head', icon(ICONS.gear, 'bs-set__gear'), h('h2', 'bs-display', 'SETTINGS'), close),
      h(
        'div',
        'bs-set__body',
        h(
          'div',
          'bs-set__col',
          h('h3', 'bs-set__section', 'Camera & tracking'),
          row('Camera', this.camSelect),
          row('Pose model', modelSeg.el),
          row('Mirror camera', this.mirror.btn),
          row('Show webcam in game', this.webcam.btn),
          row('Copy my moves', this.mimic.btn, "Runner's arms follow yours"),
        ),
        h(
          'div',
          'bs-set__col',
          h('h3', 'bs-set__section', 'Game'),
          row('Graphics quality', qualitySeg.el),
          row('Tutorial', this.tutorial.btn),
          row('Mute', this.mute.btn),
          row('Music volume', sliderWrap(this.music)),
          row('Sound effects', sliderWrap(this.sfx)),
        ),
        advanced,
      ),
      h('footer', 'bs-set__foot', reset, h('p', 'bs-set__privacy', 'Crash selfies stay in this browser only.'), askEl, confirmEl),
    );
    const backdrop = h('div', 'bs-set__backdrop');
    backdrop.addEventListener('click', () => emit({ type: 'settings-open', open: false }));
    this.root = h('div', 'bs-set', backdrop, panel);
    this.show = new Show(this.root);
    this.show.set(false);
    this.syncConfirm();
  }

  private change(s: Partial<Settings>): void {
    this.emit({ type: 'settings-change', settings: s });
  }

  private changeVision(v: Partial<VisionTuning>): void {
    this.change({ vision: { ...this.settings.vision, ...v } });
  }

  private segmented(values: string[], labels: string[], onPick: (v: string) => void): { el: HTMLElement; seg: Segmented } {
    const el = h('div', 'bs-seg');
    const btns = new Map<string, Flag>();
    values.forEach((v, i) => {
      const b = button('bs-seg__btn', labels[i]);
      b.addEventListener('click', () => onPick(v));
      el.append(b);
      btns.set(v, new Flag(b, 'is-on'));
    });
    return { el, seg: { btns } };
  }

  private toggle(onSet: (on: boolean) => void): Toggle {
    const label = h('span', 'bs-toggle__label');
    const btn = button('bs-toggle', h('span', 'bs-toggle__knob'), label);
    const t: Toggle = { btn, on: new Flag(btn, 'is-on'), label: new Text(label) };
    btn.addEventListener('click', () => onSet(!t.on.value));
    return t;
  }

  private slider(min: number, max: number, step: number, onInput: (v: number) => void, fmt: (v: number) => string): Slider {
    const input = h('input', 'bs-range');
    input.type = 'range';
    input.min = String(min);
    input.max = String(max);
    input.step = String(step);
    const value = new Text(h('span', 'bs-range__val'));
    const s: Slider = { input, value, last: Number.NaN, fmt };
    input.addEventListener('input', () => {
      const v = Number(input.value);
      s.last = v;
      value.set(fmt(v));
      onInput(v);
    });
    input.addEventListener('change', () => input.blur());
    input.addEventListener('keydown', (e) => e.stopPropagation());
    return s;
  }

  private syncSlider(s: Slider, v: number): void {
    if (document.activeElement === s.input) return;
    if (v === s.last) return;
    s.last = v;
    s.input.value = String(v);
    s.value.set(s.fmt(v));
  }

  private syncToggle(t: Toggle, on: boolean): void {
    t.on.set(on);
    t.label.set(on ? 'ON' : 'OFF');
  }

  private syncConfirm(): void {
    const asking = performance.now() < this.confirmUntil;
    this.clearAsk.set(!asking);
    this.clearConfirm.set(asking);
  }

  update(m: UIModel): void {
    const open = m.settingsOpen;
    this.show.set(open);
    if (!open) {
      if (this.wasOpen) {
        this.confirmUntil = 0;
        this.syncConfirm();
        const a = document.activeElement;
        if (a instanceof HTMLElement && this.root.contains(a)) a.blur();
      }
      this.wasOpen = false;
      return;
    }
    this.wasOpen = true;
    const s = m.settings;
    this.settings = s;

    // Camera list (rebuilt only when the list or selection changes).
    let key = s.cameraId ?? '';
    for (const c of m.cameras) key += `|${c.deviceId}:${c.label}`;
    if (key !== this.camKey) {
      this.camKey = key;
      this.camSelect.replaceChildren();
      const def = h('option', null, 'Default camera');
      def.value = '';
      this.camSelect.append(def);
      m.cameras.forEach((c, i) => {
        const o = h('option', null, c.label || `Camera ${i + 1}`);
        o.value = c.deviceId;
        this.camSelect.append(o);
      });
      this.camSelect.value = s.cameraId !== null && m.cameras.some((c) => c.deviceId === s.cameraId) ? s.cameraId : '';
    }

    for (const [v, f] of this.model.btns) f.set(v === s.model);
    for (const [v, f] of this.quality.btns) f.set(v === s.quality);
    this.syncToggle(this.mute, s.muted);
    this.syncToggle(this.tutorial, s.tutorial);
    this.syncToggle(this.webcam, s.showWebcam);
    this.syncToggle(this.mimic, s.mimic);
    this.syncToggle(this.mirror, s.vision.mirror);
    this.syncSlider(this.music, s.musicVolume);
    this.syncSlider(this.sfx, s.sfxVolume);
    for (const [k, sl] of this.vision) this.syncSlider(sl, s.vision[k]);
    if (this.confirmUntil > 0 && performance.now() >= this.confirmUntil) {
      this.confirmUntil = 0;
      this.syncConfirm();
    }
  }
}

function pct(v: number): string {
  return `${Math.round(v * 100)}%`;
}

function sliderWrap(s: Slider): HTMLElement {
  return h('div', 'bs-range__wrap', s.input, s.value.el);
}

function row(label: string, control: HTMLElement, help?: string): HTMLElement {
  return h('div', 'bs-set__row', h('div', 'bs-set__label', h('span', null, label), help ? h('small', null, help) : null), control);
}
