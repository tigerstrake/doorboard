import { useEffect, useRef, useState } from "react";
import Keyboard from "react-simple-keyboard";
import "react-simple-keyboard/build/css/index.css";

interface OnScreenKeyboardProps {
  /** Current text value (used to seed the keyboard buffer on open). */
  value: string;
  /** Called with the full updated string on every keypress. */
  onChange: (value: string) => void;
  /** Called when the user taps Done. */
  onClose: () => void;
  /** Optional hard cap on length (e.g. 280 for the guestbook). */
  maxLength?: number;
}

// Touch-friendly layout. Function keys use react-simple-keyboard conventions.
const LAYOUT = {
  default: [
    "1 2 3 4 5 6 7 8 9 0 {bksp}",
    "q w e r t y u i o p",
    "a s d f g h j k l '",
    "{shift} z x c v b n m , . ?",
    "{space} {done}",
  ],
  shift: [
    "! @ # $ % & * ( ) {bksp}",
    "Q W E R T Y U I O P",
    'A S D F G H J K L "',
    "{shift} Z X C V B N M ; : -",
    "{space} {done}",
  ],
};

const DISPLAY = {
  "{bksp}": "⌫",
  "{shift}": "⇧",
  "{space}": "space",
  "{done}": "Done",
};

/**
 * On-screen keyboard for the touch kiosk (no physical keyboard). Renders as a
 * fixed bottom panel; keypresses drive the bound text field via `onChange`.
 * The kiosk Chromium runs under XWayland, so a system Wayland OSK can't auto-
 * show for it — this in-app keyboard is the reliable path.
 */
export function OnScreenKeyboard({ value, onChange, onClose, maxLength }: OnScreenKeyboardProps) {
  const keyboardRef = useRef<{ setInput: (v: string) => void } | null>(null);
  const [layoutName, setLayoutName] = useState<"default" | "shift">("default");

  // Seed the keyboard's internal buffer with the existing text when it opens.
  useEffect(() => {
    keyboardRef.current?.setInput(value);
    // Intentionally run once on mount only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleChange = (input: string) => {
    onChange(maxLength ? input.slice(0, maxLength) : input);
  };

  const handleKeyPress = (button: string) => {
    if (button === "{shift}") {
      setLayoutName((prev) => (prev === "default" ? "shift" : "default"));
    } else if (button === "{done}") {
      onClose();
    }
  };

  return (
    <div className="osk-panel" role="group" aria-label="On-screen keyboard">
      <div className="osk-inner">
        <Keyboard
          keyboardRef={(r) => {
            keyboardRef.current = r;
          }}
          layoutName={layoutName}
          layout={LAYOUT}
          display={DISPLAY}
          onChange={handleChange}
          onKeyPress={handleKeyPress}
          preventMouseDownDefault
          disableCaretPositioning
        />
      </div>
    </div>
  );
}
