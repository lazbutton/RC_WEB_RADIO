import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { BrandContext } from "./lib/BrandContext";
import { loadBrand, type Brand } from "./lib/brand";
import "./styles/tokens.css";
import "./styles/base.css";
import "./styles/components.css";
import "./styles/shell.css";
import "./styles/queue.css";
import "./styles/settings.css";
import "./styles/motion.css";
import "./styles/pages.css";

function Root() {
  const [brand, setBrand] = useState<Brand | null>(null);
  useEffect(() => {
    loadBrand().then((data) => {
      document.title = "Régie · Radio Campus Orléans";
      setBrand(data);
    });
  }, []);
  if (!brand) return null;
  return (
    <BrandContext.Provider value={brand}>
      <App />
    </BrandContext.Provider>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
);
