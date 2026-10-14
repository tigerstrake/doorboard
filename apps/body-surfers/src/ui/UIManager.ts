// DOM overlay for every screen. The App builds a UIModel each frame; render() diffs it via
// cached bindings (see dom.ts) so the DOM is touched only when something changes.
import '@fontsource/lilita-one';
import '@fontsource/nunito/700.css';
import '@fontsource/nunito/900.css';
import './ui.css';
import type { IUI, Screen, UICommand, UIModel } from '../shared/types';
import { Emitter } from '../shared/emitter';
import { Attr, Flag, h, Show } from './dom';
import type { CamMode } from './config';
import type { ScreenView } from './views/common';
import { BootView } from './views/BootView';
import { LobbyView } from './views/LobbyView';
import { CountdownView } from './views/CountdownView';
import { PausedView } from './views/PausedView';
import { GameOverView } from './views/GameOverView';
import { HudView, type HudPhase } from './views/HudView';
import { WebcamOverlay } from './views/WebcamOverlay';
import { SettingsPanel } from './views/SettingsPanel';
import { CornerButtons, DebugOverlay, Toast } from './views/Chrome';
import { Announcer } from './announcer';

interface Built {
  root: HTMLElement;
  screens: Record<Screen, ScreenView | null>;
  shows: Map<ScreenView, Show>;
  hud: HudView;
  hudShow: Show;
  cam: WebcamOverlay;
  camDock: HTMLElement;
  settings: SettingsPanel;
  debug: DebugOverlay;
  toast: Toast;
  announcer: Announcer;
  corner: CornerButtons;
  screenAttr: Attr;
  playersAttr: Attr;
  settingsFlag: Flag;
}

export class UIManager implements IUI {
  private readonly commands = new Emitter<UICommand>();
  private ui: Built | null = null;
  private current: Screen | null = null;
  private camParent: HTMLElement | null = null;
  private readonly slots = new Map<string, HTMLElement | null>();

  init(root: HTMLElement): void {
    if (this.ui) this.dispose();
    const emit = (c: UICommand): void => this.commands.emit(c);
    const boot = new BootView(emit);
    const lobby = new LobbyView(emit);
    const countdown = new CountdownView(emit);
    const paused = new PausedView(emit);
    const gameover = new GameOverView(emit);
    const hud = new HudView(emit);
    const cam = new WebcamOverlay();
    const settings = new SettingsPanel(emit);
    const debug = new DebugOverlay();
    const toast = new Toast();
    const announcer = new Announcer();
    const corner = new CornerButtons(emit);
    const camDock = h('div', 'bs-camdock');

    const screens: Record<Screen, ScreenView | null> = { boot, lobby, countdown, playing: null, paused, gameover };
    const shows = new Map<ScreenView, Show>();
    const layer = h('div', 'bs-screens');
    for (const v of [boot, lobby, countdown, paused, gameover]) {
      layer.append(v.root);
      const s = new Show(v.root);
      s.set(false);
      shows.set(v, s);
    }
    const ui = h('div', 'bs-ui', hud.root, layer, camDock, toast.root, corner.root, settings.root, debug.root, announcer.root);
    root.append(ui);
    camDock.append(cam.root);
    this.camParent = camDock;
    const hudShow = new Show(hud.root);
    hudShow.set(false);
    this.ui = {
      root: ui,
      screens,
      shows,
      hud,
      hudShow,
      cam,
      camDock,
      settings,
      debug,
      toast,
      announcer,
      corner,
      screenAttr: new Attr(ui, 'data-screen'),
      playersAttr: new Attr(ui, 'data-players'),
      settingsFlag: new Flag(ui, 'is-settings'),
    };
    this.current = null;
  }

  render(model: UIModel, dt: number): void {
    const ui = this.ui;
    if (!ui) return;
    const now = performance.now();
    const screen = model.screen;
    if (screen !== this.current) {
      const prev = this.current ? ui.screens[this.current] : null;
      if (prev) {
        prev.leave?.();
        ui.shows.get(prev)?.set(false);
      }
      const next = ui.screens[screen];
      if (next) {
        next.enter?.(model);
        ui.shows.get(next)?.set(true);
      }
      this.current = screen;
      ui.screenAttr.set(screen);
    }
    ui.playersAttr.set(String(model.playerCount));
    ui.settingsFlag.set(model.settingsOpen);

    const view = ui.screens[screen];
    if (view) view.update(model, now, dt);

    const hudPhase: HudPhase | null = screen === 'countdown' || screen === 'playing' || screen === 'paused' ? screen : null;
    ui.hudShow.set(hudPhase !== null);
    if (hudPhase) ui.hud.update(model, hudPhase);

    this.placeCamera(model);
    ui.cam.update(model, now);
    ui.toast.update(model, now);
    ui.announcer.update(model);
    ui.corner.update(model);
    ui.settings.update(model);
    ui.debug.update(model, now);
  }

  private placeCamera(m: UIModel): void {
    const ui = this.ui;
    if (!ui) return;
    const show = m.settings.showWebcam;
    const keyboardNoVideo = m.inputMode === 'keyboard' && m.vision.video === null;
    let mode: CamMode = 'hidden';
    let slot: HTMLElement | null = null;
    const find = (sel: string): HTMLElement | null => {
      let el = this.slots.get(sel);
      if (el === undefined) {
        el = ui.root.querySelector<HTMLElement>(sel);
        this.slots.set(sel, el);
      }
      return el;
    };
    switch (m.screen) {
      case 'boot':
        mode = 'hidden';
        break;
      case 'lobby':
        mode = keyboardNoVideo ? 'hidden' : 'lobby';
        slot = find('.bs-lobby__camslot');
        break;
      case 'countdown':
        mode = keyboardNoVideo ? 'hidden' : 'countdown';
        slot = find('.bs-count__camslot');
        break;
      case 'playing':
        mode = show && !keyboardNoVideo ? (m.playerCount === 2 ? 'top' : 'corner') : 'hidden';
        break;
      case 'paused':
        mode = !keyboardNoVideo && (show || m.paused?.reason === 'tracking-lost') ? 'paused' : 'hidden';
        slot = find('.bs-paused__camslot');
        break;
      case 'gameover':
        mode = show && !keyboardNoVideo ? 'mini' : 'hidden';
        slot = find('.bs-go__camslot');
        break;
    }
    const parent = mode === 'hidden' ? this.camParent : slot ?? ui.camDock;
    if (parent && parent !== this.camParent) {
      parent.append(ui.cam.root);
      this.camParent = parent;
    }
    ui.cam.setMode(mode);
  }

  onCommand(cb: (c: UICommand) => void): () => void {
    return this.commands.on(cb);
  }

  dispose(): void {
    if (this.ui) {
      this.ui.cam.dispose();
      this.ui.root.remove();
    }
    this.ui = null;
    this.current = null;
    this.camParent = null;
    this.slots.clear();
    this.commands.clear();
  }
}
