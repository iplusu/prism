import { useCallback, useEffect, useMemo, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { api, errorMessage, onUnauthorized } from "./api";
import { AppContext, type AppCtx } from "./appContext";
import { ErrorBox, Loading } from "./components/ui";
import LoginPage from "./pages/LoginPage";
import ProjectsPage from "./pages/ProjectsPage";
import ReviewPage from "./pages/ReviewPage";
import SettingsPage from "./pages/SettingsPage";
import WorkspacePage from "./pages/WorkspacePage";
import { useTheme } from "./theme";
import type { AuthConfig, User } from "./types";

export default function App() {
  const theme = useTheme();
  const [config, setConfig] = useState<AuthConfig | null>(null);
  const [user, setUser] = useState<User | null | undefined>(undefined); // undefined = checking
  const [bootError, setBootError] = useState<string | null>(null);

  const boot = useCallback(async () => {
    setBootError(null);
    try {
      const cfg = await api.authConfig();
      setConfig(cfg);
    } catch (e) {
      setBootError(errorMessage(e));
      return;
    }
    try {
      setUser(await api.me());
    } catch {
      setUser(null);
    }
  }, []);

  useEffect(() => {
    void boot();
  }, [boot]);

  // Any 401 from the API means the session ended: show the login screen.
  useEffect(() => onUnauthorized(() => setUser(null)), []);

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } finally {
      setUser(null);
    }
  }, []);

  const ctx: AppCtx | null = useMemo(
    () => (user && config ? { user, config, logout, theme } : null),
    [user, config, logout, theme],
  );

  if (bootError) {
    return (
      <div className="center-page">
        <div className="card narrow">
          <h1>Prism</h1>
          <ErrorBox error={bootError} onRetry={boot} />
        </div>
      </div>
    );
  }
  if (!config || user === undefined) return <Loading text="Starting Prism…" />;

  return (
    <div className="app-root">
      {config.demo_mode && (
        <div className="demo-banner" role="status">
          DEMO MODE — the AI assistant uses a rule-based demo executor, not a real language model or coding tool.
        </div>
      )}
      {!ctx ? (
        <LoginPage config={config} onLogin={setUser} theme={theme} />
      ) : (
        <AppContext.Provider value={ctx}>
          <Routes>
            <Route path="/" element={<ProjectsPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/p/:pid" element={<WorkspacePage />} />
            <Route path="/p/:pid/settings" element={<SettingsPage />} />
            <Route path="/p/:pid/review" element={<ReviewPage />} />
            <Route path="/p/:pid/review/:propId" element={<ReviewPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </AppContext.Provider>
      )}
    </div>
  );
}
