// clear.html: the owner's "clear the Body Surfers leaderboard" (ADR-0049). The board
// lives in this origin's localStorage inside the wallboard kiosk, so the wallboard
// loads this page in a hidden frame to wipe it; no game has to be running. A game
// that is running picks the change up through its 'storage' listener.
import { browserStorage } from '../app/storage';
import { STORAGE_KEYS } from '../shared/constants';
import { parseParams } from '../shared/params';
import { DoorBridge } from './bridge';

const storage = browserStorage();
let cleared = false;
try {
  storage.removeItem(STORAGE_KEYS.leaderboard);
  cleared = storage.getItem(STORAGE_KEYS.leaderboard) === null;
} catch (e) {
  console.warn('[door] could not clear the leaderboard:', e);
}
if (cleared) new DoorBridge(window, parseParams().door?.parentOrigin ?? null).post({ type: 'leaderboard-cleared' });
