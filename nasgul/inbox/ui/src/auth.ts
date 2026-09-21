import { createContext, useContext } from "react";

type AuthCtx = {
  onLost: () => void;
};

export const AuthContext = createContext<AuthCtx>({ onLost: () => undefined });

export function useAuth(): AuthCtx {
  return useContext(AuthContext);
}
