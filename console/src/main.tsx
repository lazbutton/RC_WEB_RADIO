import "@radix-ui/themes/styles.css";
import { Theme } from "@radix-ui/themes";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App } from "./App";
import { StationProvider } from "./mock/StationContext";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Theme
      appearance="dark"
      accentColor="ruby"
      grayColor="mauve"
      radius="medium"
      scaling="100%"
      panelBackground="translucent"
    >
      <StationProvider>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </StationProvider>
    </Theme>
  </StrictMode>,
);
