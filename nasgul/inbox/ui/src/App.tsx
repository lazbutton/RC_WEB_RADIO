import { useCallback, useEffect, useState } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthError, getQueue } from "./api";
import { AuthContext } from "./auth";
import { ToastProvider } from "./components/Toast";
import { Shell } from "./layout/Shell";
import { StoreProvider } from "./lib/store";
import { LoginPage } from "./pages/Login";
import { SettingsPage } from "./pages/Settings";
import { QueuePage } from "./pages/queue/QueuePage";

export function App() {
  const [authed, setAuthed] = useState<boolean | null>(null);
  const onLost = useCallback(() => setAuthed(false), []);

  useEffect(() => {
    getQueue()
      .then(() => setAuthed(true))
      .catch((err) => {
        setAuthed(false);
        if (!(err instanceof AuthError)) console.warn(err);
      });
  }, []);

  if (authed === null) return <div className="boot" aria-busy="true" />;

  if (!authed) {
    return <LoginPage onOk={() => setAuthed(true)} />;
  }

  return (
    <AuthContext.Provider value={{ onLost }}>
      <ToastProvider>
        <StoreProvider>
          <BrowserRouter>
            <Routes>
              <Route element={<Shell />}>
                <Route path="/" element={<QueuePage view="queue" />} />
                <Route path="/history" element={<QueuePage view="history" />} />
                <Route path="/settings" element={<SettingsPage />} />
                <Route path="*" element={<Navigate to="/" replace />} />
              </Route>
            </Routes>
          </BrowserRouter>
        </StoreProvider>
      </ToastProvider>
    </AuthContext.Provider>
  );
}
