import { useCallback, useEffect, useState } from "react";

export type ThemePref = "light" | "dark" | "system";
const KEY = "prism-theme";

function readPref(): ThemePref {
  try {
    const v = localStorage.getItem(KEY);
    if (v === "light" || v === "dark") return v;
  } catch {
    /* storage unavailable */
  }
  return "system";
}

function systemDark(): boolean {
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

/** Theme preference persisted in localStorage; defaults to the OS setting. */
export function useTheme() {
  const [pref, setPref] = useState<ThemePref>(readPref);
  const [sysDark, setSysDark] = useState(systemDark);

  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const on = () => setSysDark(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);

  const effective: "light" | "dark" = pref === "system" ? (sysDark ? "dark" : "light") : pref;

  useEffect(() => {
    document.documentElement.dataset.theme = effective;
  }, [effective]);

  const choose = useCallback((p: ThemePref) => {
    setPref(p);
    try {
      if (p === "system") localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, p);
    } catch {
      /* ignore */
    }
  }, []);

  /** Cycle: system -> opposite of system -> system's own -> system. Simple two-way toggle from the user's view. */
  const toggle = useCallback(() => {
    choose(effective === "dark" ? "light" : "dark");
  }, [choose, effective]);

  return { pref, effective, choose, toggle };
}
