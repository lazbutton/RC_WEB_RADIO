import type { Brand } from "../vite-env";

export async function loadBrand(): Promise<Brand> {
  const response = await fetch("./brand.json", { cache: "no-store" });
  if (!response.ok) throw new Error("brand.json introuvable");
  return (await response.json()) as Brand;
}
