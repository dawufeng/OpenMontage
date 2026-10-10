/**
 * Local font loader — replaces @remotion/google-fonts with fonts shipped in
 * the public/ directory so rendering works without fonts.gstatic.com access.
 */
import { staticFile } from "remotion";

interface FontDef {
  url: string;
  weight: string;
  style: string;
  subset: string;
}

interface FontFamily {
  fontFamily: string;
  fonts: FontDef[];
}

interface LoadOptions {
  weights?: string[];
  subsets?: string[];
}

function loadLocalFonts(
  family: FontFamily,
  style: string,
  options?: LoadOptions,
): { fontFamily: string } {
  if (typeof document === "undefined") {
    return { fontFamily: family.fontFamily };
  }

  const filtered = family.fonts.filter((f) => {
    if (f.style !== style) return false;
    if (options?.weights?.length && !options.weights.includes(f.weight)) return false;
    if (options?.subsets?.length && !options.subsets.includes(f.subset)) return false;
    return true;
  });

  for (const def of filtered) {
    const fontFace = new FontFace(
      family.fontFamily,
      `url(${staticFile(def.url)}) format('woff2')`,
      { weight: def.weight, style: def.style },
    );
    fontFace.load().then(() => {
      document.fonts.add(fontFace);
    });
  }

  return { fontFamily: family.fontFamily };
}

// --- Space Grotesk ---
const spaceGrotesk: FontFamily = {
  fontFamily: "Space Grotesk",
  fonts: [
    { url: "fonts/space-grotesk-latin-400-normal.woff2", weight: "400", style: "normal", subset: "latin" },
    { url: "fonts/space-grotesk-latin-500-normal.woff2", weight: "500", style: "normal", subset: "latin" },
    { url: "fonts/space-grotesk-latin-700-normal.woff2", weight: "700", style: "normal", subset: "latin" },
  ],
};

export function loadSpaceGrotesk(
  style = "normal",
  options?: LoadOptions,
): { fontFamily: string } {
  return loadLocalFonts(spaceGrotesk, style, options);
}

// --- Playfair Display ---
const playfairDisplay: FontFamily = {
  fontFamily: "Playfair Display",
  fonts: [
    { url: "fonts/playfair-display-latin-400-normal.woff2", weight: "400", style: "normal", subset: "latin" },
    { url: "fonts/playfair-display-latin-400-italic.woff2", weight: "400", style: "italic", subset: "latin" },
    { url: "fonts/playfair-display-latin-700-normal.woff2", weight: "700", style: "normal", subset: "latin" },
    { url: "fonts/playfair-display-latin-700-italic.woff2", weight: "700", style: "italic", subset: "latin" },
    { url: "fonts/playfair-display-latin-900-normal.woff2", weight: "900", style: "normal", subset: "latin" },
  ],
};

export function loadPlayfairDisplay(
  style = "normal",
  options?: LoadOptions,
): { fontFamily: string } {
  return loadLocalFonts(playfairDisplay, style, options);
}
