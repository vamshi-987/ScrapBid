import { useEffect, useState } from "react";
import { Route, Routes } from "react-router";

import { ensureCsrfCookie, fetchMe, logout, type Me } from "./api/auth";
import { AppShell } from "./components/AppShell";
import { DealerPage } from "./pages/DealerPage";
import { HomePage } from "./pages/HomePage";
import { LoginPage } from "./pages/LoginPage";
import { chooseView, rememberView, rememberedView, type View } from "./session/views";
import { strings } from "./strings";

export function App() {
  // undefined while loading, null when nobody is logged in.
  const [me, setMe] = useState<Me | null | undefined>(undefined);
  const [view, setView] = useState<View | null>(null);

  function handleLoggedIn(user: Me | null) {
    setMe(user);
    setView(user ? chooseView(user.roles, rememberedView()) : null);
  }

  useEffect(() => {
    ensureCsrfCookie()
      .then(fetchMe)
      .then(handleLoggedIn)
      .catch(() => handleLoggedIn(null));
  }, []);

  function handleViewChange(next: View) {
    rememberView(next);
    setView(next);
  }

  async function handleLogout() {
    await logout();
    handleLoggedIn(null);
  }

  async function refreshMe() {
    // Applying as a dealer adds the dealer role, which unlocks the Buying view.
    handleLoggedIn(await fetchMe());
  }

  return (
    <AppShell me={me} view={view} onViewChange={handleViewChange} onLogout={handleLogout}>
      <Routes>
        <Route path="/" element={<HomePage me={me} view={view} />} />
        <Route path="/login" element={<LoginPage onLoggedIn={handleLoggedIn} />} />
        <Route path="/dealer" element={<DealerPage onApplied={refreshMe} />} />
        <Route
          path="*"
          element={
            <main className="page">
              <h1>{strings.notFound}</h1>
            </main>
          }
        />
      </Routes>
    </AppShell>
  );
}
