import { App } from './app/App';
import { parseParams } from './shared/params';

const params = parseParams();

/** Phones / tablets can't play this (it needs a laptop webcam and room to move). */
function isPhone(): boolean {
  try {
    return window.matchMedia('(pointer: coarse)').matches && window.innerWidth < 900;
  } catch {
    return false;
  }
}

function renderPhoneGate(): void {
  const app = document.getElementById('app');
  if (app) app.style.display = 'none';
  const card = document.createElement('main');
  card.setAttribute('style', [
    'position:fixed', 'inset:0', 'display:flex', 'flex-direction:column', 'align-items:center', 'justify-content:center',
    'gap:20px', 'padding:24px', 'box-sizing:border-box', 'background:#0b1020', 'color:#e8ecff', 'text-align:center',
    'font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif',
  ].join(';'));
  const title = document.createElement('h1');
  title.textContent = 'BODY SURFERS';
  title.setAttribute('style', 'margin:0;font-size:clamp(36px,11vw,64px);font-weight:900;letter-spacing:1px;color:#ffd23f;text-shadow:0 4px 0 #b8860b');
  const text = document.createElement('p');
  text.textContent = 'You play this with your whole body using a laptop webcam. Open this link on a computer.';
  text.setAttribute('style', 'margin:0;max-width:420px;font-size:19px;line-height:1.45');
  const link = document.createElement('a');
  link.href = '?input=keyboard&autostart=1&autopilot=1&mute=1';
  link.textContent = 'or watch the AI play';
  link.setAttribute('style', 'color:#9fb4ff;font-size:15px;text-decoration:underline');
  card.append(title, text, link);
  document.body.append(card);
}

if (!params.autopilot && isPhone()) {
  renderPhoneGate();
} else {
  const app = new App(params);
  app.start().catch((err) => console.error('[app] failed to start', err));
}
