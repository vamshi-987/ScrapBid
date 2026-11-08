import { Route, Routes } from "react-router";

import { HomePage } from "./pages/HomePage";
import { strings } from "./strings";

export function App() {
  return (
    <Routes>
      <Route path="/" element={<HomePage />} />
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
