import { createContext, useContext } from "react";
import type { AuthConfig, User } from "./types";
import type { ThemePref } from "./theme";

export interface AppCtx {
  user: User;
  config: AuthConfig;
  logout: () => Promise<void>;
  theme: { pref: ThemePref; effective: "light" | "dark"; toggle: () => void; choose: (p: ThemePref) => void };
}

export const AppContext = createContext<AppCtx | null>(null);

export function useApp(): AppCtx {
  const c = useContext(AppContext);
  if (!c) throw new Error("AppContext missing");
  return c;
}
