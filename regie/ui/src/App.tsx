import { useCallback, useEffect, useState } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthError } from "./api";
import { me } from "./api/client";
import { AuthContext } from "./auth";
import { ToastProvider } from "./components/Toast";
import { Shell } from "./layout/Shell";
import { RegistryProvider } from "./lib/registry";
import { StoreProvider } from "./lib/store";
import { FilesPage } from "./pages/FilesPage";
import { LoginPage } from "./pages/Login";
import { SettingsPage } from "./pages/Settings";
import { SystemPage } from "./pages/SystemPage";
import { TodayPage } from "./pages/Today";
import { ContactsPage } from "./pages/contacts/ContactsPage";
import { EntityPage } from "./pages/contacts/EntityPage";
import { CoverageBoard, EventPage, EventsPage } from "./pages/events/EventsPage";
import { PlanningPage } from "./pages/planning/PlanningPage";
import { WeekPage } from "./pages/planning/WeekPage";
import { PublishPage } from "./pages/publish/PublishPage";
import { QueuePage } from "./pages/queue/QueuePage";
import { RadioPage } from "./pages/radio/RadioPage";
import { EpisodePage } from "./pages/shows/EpisodePage";
import { PodcastPage, ShowsPage } from "./pages/shows/ShowsPage";

export function App() {
  const [authed, setAuthed] = useState<boolean | null>(null);
  const onLost = useCallback(() => setAuthed(false), []);

  useEffect(() => {
    me()
      .then(() => setAuthed(true))
      .catch((err) => {
        setAuthed(false);
        if (!(err instanceof AuthError)) console.warn(err);
      });
  }, []);

  if (authed === null) return <div className="boot" aria-busy="true" />;
  if (!authed) return <LoginPage onOk={() => setAuthed(true)} />;

  return (
    <AuthContext.Provider value={{ onLost }}>
      <ToastProvider>
        <RegistryProvider>
          <StoreProvider>
            <BrowserRouter>
              <Routes>
                <Route element={<Shell />}>
                  <Route path="/" element={<TodayPage />} />
                  <Route path="/mails" element={<QueuePage view="queue" />} />
                  <Route path="/mails/history" element={<QueuePage view="history" />} />
                  <Route path="/contacts" element={<ContactsPage />} />
                  <Route path="/contacts/:kind/:id" element={<EntityPage />} />
                  <Route path="/events" element={<EventsPage />} />
                  <Route path="/events/coverage" element={<CoverageBoard />} />
                  <Route path="/events/:id" element={<EventPage />} />
                  <Route path="/planning" element={<PlanningPage />} />
                  <Route path="/planning/week" element={<WeekPage />} />
                  <Route path="/shows" element={<ShowsPage />} />
                  <Route path="/shows/episodes/:id" element={<EpisodePage />} />
                  <Route path="/shows/podcasts/:id" element={<PodcastPage />} />
                  <Route path="/shows/:id" element={<ShowsPage />} />
                  <Route path="/publish" element={<PublishPage />} />
                  <Route path="/radio" element={<RadioPage />} />
                  <Route path="/files" element={<FilesPage />} />
                  <Route path="/system" element={<SystemPage />} />
                  <Route path="/settings" element={<SettingsPage />} />
                  <Route path="/history" element={<Navigate to="/mails/history" replace />} />
                  <Route path="*" element={<Navigate to="/" replace />} />
                </Route>
              </Routes>
            </BrowserRouter>
          </StoreProvider>
        </RegistryProvider>
      </ToastProvider>
    </AuthContext.Provider>
  );
}
