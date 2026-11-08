import { useEffect, useState } from "react";
import { Route, Routes } from "react-router";

import { ensureCsrfCookie, fetchMe, logout, type Me } from "./api/auth";
import { HomePage } from "./pages/HomePage";
import { LoginPage } from "./pages/LoginPage";
import { strings } from "./strings";

export function App() {
  // undefined while loading, null when nobody is logged in.
  const [me, setMe] = useState<Me | null | undefined>(undefined);

  useEffect(() => {
    ensureCsrfCookie()
      .then(fetchMe)
      .then(setMe)
      .catch(() => setMe(null));
  }, []);

  async function handleLogout() {
    await logout();
    setMe(null);
  }

  return (
    <Routes>
      <Route path="/" element={<HomePage me={me} onLogout={handleLogout} />} />
      <Route path="/login" element={<LoginPage onLoggedIn={setMe} />} />
      <Route
        path="*"
        element={
          <main className="page">
            <h1>{strings.notFound}</h1>
          </main>
        }
      />
    </Routes>
  );
}
