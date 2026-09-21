import { createContext, useContext } from "react";
import type { Brand } from "./brand";

export const BrandContext = createContext<Brand | null>(null);

export function useBrand(): Brand {
  const brand = useContext(BrandContext);
  if (!brand) throw new Error("brand");
  return brand;
}
