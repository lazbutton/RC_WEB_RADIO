import { Navigate, Route, Routes } from "react-router-dom";
import { Shell } from "./layout/Shell";
import { AntennePage } from "./pages/AntennePage";
import { CategoriesPage } from "./pages/CategoriesPage";
import { ConducteurPage } from "./pages/ConducteurPage";
import { HabillagePage } from "./pages/HabillagePage";
import { HorlogesPage } from "./pages/HorlogesPage";
import { SemainePage } from "./pages/SemainePage";

export function App() {
  return (
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<Navigate to="/antenne" replace />} />
        <Route path="antenne" element={<AntennePage />} />
        <Route path="horloges" element={<HorlogesPage />} />
        <Route path="semaine" element={<SemainePage />} />
        <Route path="conducteur" element={<ConducteurPage />} />
        <Route path="categories" element={<CategoriesPage />} />
        <Route path="habillage" element={<HabillagePage />} />
      </Route>
    </Routes>
  );
}
