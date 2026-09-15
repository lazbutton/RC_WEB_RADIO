export type Kind = "musique" | "jingle" | "son" | "pub";

export type CatalogItem = {
  id: string;
  kind: Kind;
  title: string;
  artist?: string;
  durationSec: number;
  cart?: "Jingles NTR" | "Pubs" | "Promos";
  category?: "Rotation" | "Archives";
};

export type ClockMotifStep = {
  kind: Kind;
  category?: "Rotation" | "Archives";
  cart?: CatalogItem["cart"];
};

export type ClockDef = {
  id: "journee" | "soir";
  name: string;
  motif: ClockMotifStep[];
  anchors: { minute: 20 | 40; kind: "pub"; cart: "Pubs"; sync: "dure" }[];
};

export const CATEGORIES = [
  {
    name: "Rotation" as const,
    query: "grouping:rotation",
    fallback: "Music/",
  },
  {
    name: "Archives" as const,
    query: "grouping:ntf1,ntf2,ntf3",
    fallback: "grouping:rotation",
  },
];

export const CLOCKS: ClockDef[] = [
  {
    id: "journee",
    name: "Journée avec pubs",
    motif: [
      { kind: "jingle", cart: "Jingles NTR" },
      { kind: "musique", category: "Rotation" },
      { kind: "musique", category: "Rotation" },
    ],
    anchors: [
      { minute: 20, kind: "pub", cart: "Pubs", sync: "dure" },
      { minute: 40, kind: "pub", cart: "Pubs", sync: "dure" },
    ],
  },
  {
    id: "soir",
    name: "Soir habillé",
    motif: [
      { kind: "jingle", cart: "Jingles NTR" },
      { kind: "musique", category: "Rotation" },
      { kind: "musique", category: "Rotation" },
    ],
    anchors: [],
  },
];

export const CATALOG: CatalogItem[] = [
  { id: "m01", kind: "musique", artist: "Les Veilleurs", title: "Contretemps", durationSec: 214, category: "Rotation" },
  { id: "m02", kind: "musique", artist: "Trio Penhars", title: "Souffle d’ouest", durationSec: 198, category: "Rotation" },
  { id: "m03", kind: "musique", artist: "Marie Lannion", title: "Clair de lande", durationSec: 241, category: "Rotation" },
  { id: "m04", kind: "musique", artist: "Collectif NTR", title: "Bal de minuit", durationSec: 187, category: "Rotation" },
  { id: "m05", kind: "musique", artist: "Yann Kerbrat", title: "Marée basse", durationSec: 223, category: "Rotation" },
  { id: "m06", kind: "musique", artist: "Les Brumes", title: "Route de Pontivy", durationSec: 206, category: "Rotation" },
  { id: "m07", kind: "musique", artist: "Anna Mevel", title: "Fil à fil", durationSec: 231, category: "Rotation" },
  { id: "m08", kind: "musique", artist: "Cercle de l’Aven", title: "Gavotte ronde", durationSec: 255, category: "Rotation" },
  { id: "m09", kind: "musique", artist: "Loeiz ar Mor", title: "Trois notes", durationSec: 176, category: "Rotation" },
  { id: "m10", kind: "musique", artist: "Duo Kervran", title: "Lande rouge", durationSec: 219, category: "Rotation" },
  { id: "m11", kind: "musique", artist: "Nolwenn Bihan", title: "Chant de haie", durationSec: 192, category: "Rotation" },
  { id: "m12", kind: "musique", artist: "Les Veilleurs", title: "Après la pluie", durationSec: 208, category: "Rotation" },
  { id: "m13", kind: "musique", artist: "Trio Penhars", title: "Anse du Groix", durationSec: 244, category: "Rotation" },
  { id: "m14", kind: "musique", artist: "Collectif NTR", title: "Rond de Loudéac", durationSec: 201, category: "Rotation" },
  { id: "m15", kind: "musique", artist: "Marie Lannion", title: "Fenêtre ouverte", durationSec: 227, category: "Rotation" },
  { id: "m16", kind: "musique", artist: "Yann Kerbrat", title: "Nuit de foire", durationSec: 183, category: "Rotation" },
  { id: "a01", kind: "musique", artist: "NTF#2 Plateau", title: "Entretien Kermorvan", durationSec: 312, category: "Archives" },
  { id: "a02", kind: "musique", artist: "NTF#3", title: "Veillée chapiteau", durationSec: 268, category: "Archives" },
  { id: "a03", kind: "musique", artist: "NTF#1", title: "Bal d’ouverture", durationSec: 245, category: "Archives" },
  { id: "a04", kind: "musique", artist: "NTF#3", title: "Atelier chants de mer", durationSec: 289, category: "Archives" },
  { id: "a05", kind: "musique", artist: "NTF#2", title: "Suite bombardes", durationSec: 233, category: "Archives" },
  { id: "a06", kind: "musique", artist: "NTF#1 Plateau", title: "Rencontre luthiers", durationSec: 301, category: "Archives" },
  { id: "a07", kind: "musique", artist: "NTF#3", title: "Encore de minuit", durationSec: 194, category: "Archives" },
  { id: "a08", kind: "musique", artist: "NTF#2", title: "Parade des cercles", durationSec: 221, category: "Archives" },
  { id: "j01", kind: "jingle", title: "ID New Trad Radio", durationSec: 8, cart: "Jingles NTR" },
  { id: "j02", kind: "jingle", title: "Virgule courte", durationSec: 4, cart: "Jingles NTR" },
  { id: "j03", kind: "jingle", title: "Jingle long fest", durationSec: 14, cart: "Jingles NTR" },
  { id: "j04", kind: "jingle", title: "Identifiant 2026", durationSec: 9, cart: "Jingles NTR" },
  { id: "j05", kind: "jingle", title: "Sting cuivres", durationSec: 6, cart: "Jingles NTR" },
  { id: "j06", kind: "jingle", title: "Virgule bombardes", durationSec: 5, cart: "Jingles NTR" },
  { id: "j07", kind: "jingle", title: "ID nuit", durationSec: 11, cart: "Jingles NTR" },
  { id: "j08", kind: "jingle", title: "Accroche webradio", durationSec: 7, cart: "Jingles NTR" },
  { id: "p01", kind: "pub", title: "Spot :20 brasserie", durationSec: 22, cart: "Pubs" },
  { id: "p02", kind: "pub", title: "Spot :40 festival", durationSec: 28, cart: "Pubs" },
  { id: "p03", kind: "pub", title: "Pub partenaires NTF", durationSec: 24, cart: "Pubs" },
  { id: "p04", kind: "pub", title: "Message mairie", durationSec: 20, cart: "Pubs" },
  { id: "p05", kind: "pub", title: "Habillage pub court", durationSec: 18, cart: "Pubs" },
  { id: "p06", kind: "pub", title: "Spot Labomedia", durationSec: 26, cart: "Pubs" },
  { id: "s01", kind: "son", title: "Bed plateau", durationSec: 48, cart: "Promos" },
  { id: "s02", kind: "son", title: "Promo NTF#4", durationSec: 32, cart: "Promos" },
  { id: "s03", kind: "son", title: "Sweep entrée", durationSec: 12, cart: "Promos" },
  { id: "s04", kind: "son", title: "Outro soir", durationSec: 16, cart: "Promos" },
  { id: "s05", kind: "son", title: "Virgule sonore vent", durationSec: 9, cart: "Promos" },
  { id: "s06", kind: "son", title: "Annonce grille", durationSec: 21, cart: "Promos" },
  { id: "s07", kind: "son", title: "Bed interview", durationSec: 64, cart: "Promos" },
  { id: "s08", kind: "son", title: "Promo carts live", durationSec: 19, cart: "Promos" },
];

export function itemLabel(item: CatalogItem): string {
  return item.artist ? `${item.artist} — ${item.title}` : item.title;
}

export function itemsByKind(kind: Kind): CatalogItem[] {
  return CATALOG.filter((i) => i.kind === kind);
}

export function itemsByCategory(name: "Rotation" | "Archives"): CatalogItem[] {
  return CATALOG.filter((i) => i.category === name);
}

export function itemsByCart(cart: CatalogItem["cart"]): CatalogItem[] {
  return CATALOG.filter((i) => i.cart === cart);
}

export function clockAtHour(hour: number): ClockDef {
  return hour >= 7 && hour < 19 ? CLOCKS[0] : CLOCKS[1];
}

export function kindLabel(kind: Kind): string {
  switch (kind) {
    case "musique":
      return "Musique";
    case "jingle":
      return "Jingle";
    case "son":
      return "Son";
    case "pub":
      return "Pub";
  }
}

export type KindColor = "sky" | "ruby" | "amber" | "orange";

export function kindColor(kind: Kind): KindColor {
  switch (kind) {
    case "musique":
      return "sky";
    case "jingle":
      return "ruby";
    case "son":
      return "amber";
    case "pub":
      return "orange";
  }
}
