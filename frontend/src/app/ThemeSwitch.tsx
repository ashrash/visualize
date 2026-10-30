import { useState } from "react";
import { Select } from "../components/ui/input";
import { applyThemeMode, storedThemeMode, type ThemeMode } from "../state/themeMode";

export function ThemeSwitch() {
  const [mode, setMode] = useState<ThemeMode>(() => {
    const stored = storedThemeMode();
    applyThemeMode(stored);
    return stored;
  });
  return (
    <Select
      aria-label="Theme"
      className="w-24"
      value={mode}
      options={[
        { value: "auto", label: "Auto" },
        { value: "light", label: "Light" },
        { value: "dark", label: "Dark" },
      ]}
      onChange={(event) => {
        const next = event.target.value as ThemeMode;
        applyThemeMode(next);
        setMode(next);
      }}
    />
  );
}
