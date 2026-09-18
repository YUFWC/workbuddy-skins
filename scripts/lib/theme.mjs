import { createHash } from "node:crypto";
import { copyFile, mkdir, readFile, readdir, realpath, rename, rm, stat, writeFile } from "node:fs/promises";
import { basename, extname, isAbsolute, join, relative, resolve, sep, win32 } from "node:path";

import { MAX_IMAGE_BYTES } from "./constants.mjs";

const ID = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const HEX = /^#[0-9a-f]{6}$/i;
const IMAGES = new Set([".png", ".jpg", ".jpeg", ".webp"]);
const SAFE_AREAS = new Set(["auto", "left", "right", "center", "none"]);
const APPEARANCES = new Set(["auto", "light", "dark"]);
const MATERIAL_STYLES = new Set(["ambient", "studio"]);
const MAX_DIMENSION = 16384;
const MAX_PIXELS = 50_000_000;

const DEFAULT_PALETTE = {
  accent: "#78A7FF",
  secondary: "#A78BFA",
  surface: "#11151F",
  text: "#F2F5FA",
};
const PALETTE_KEYS = Object.keys(DEFAULT_PALETTE);

function record(value, label) {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(`${label} must be an object`);
  return value;
}

function bounded(value, fallback, min = 0, max = 1) {
  const selected = value ?? fallback;
  if (!Number.isFinite(selected) || selected < min || selected > max) throw new Error(`value must be between ${min} and ${max}`);
  return selected;
}

function safeRelativeImage(value) {
  if (value == null) return null;
  if (typeof value !== "string" || !value || isAbsolute(value) || win32.isAbsolute(value) || value.split(/[\\/]+/).includes("..")) {
    throw new Error("image must be a relative path inside the theme directory");
  }
  if (!IMAGES.has(extname(value).toLowerCase())) throw new Error("image must be PNG, JPEG, or WebP");
  return value;
}

export function validateTheme(input) {
  record(input, "theme");
  if (input.schemaVersion !== 1) throw new Error("unsupported theme schema");
  if (typeof input.id !== "string" || !ID.test(input.id)) throw new Error("theme id must use lowercase letters, numbers, and hyphens");
  if (typeof input.name !== "string" || !input.name.trim()) throw new Error("theme name is required");
  const description = input.description ?? "";
  if (typeof description !== "string" || description.length > 120) throw new Error("theme description must be a string up to 120 characters");
  const appearance = input.appearance ?? "auto";
  if (!APPEARANCES.has(appearance)) throw new Error("appearance must be auto, light, or dark");
  const paletteInput = input.palette === "auto" ? "auto" : record(input.palette ?? DEFAULT_PALETTE, "palette");
  const validatePalette = (candidate, label) => Object.fromEntries(
    PALETTE_KEYS.map((key) => {
      const value = candidate[key] ?? DEFAULT_PALETTE[key];
      if (typeof value !== "string" || !HEX.test(value)) throw new Error(`${label}.${key} must be a six-digit hex color`);
      return [key, value.toUpperCase()];
    }),
  );
  let palette = "auto";
  if (paletteInput !== "auto") {
    const dual = Object.hasOwn(paletteInput, "light") || Object.hasOwn(paletteInput, "dark");
    palette = dual
      ? {
          light: validatePalette(record(paletteInput.light, "palette.light"), "palette.light"),
          dark: validatePalette(record(paletteInput.dark, "palette.dark"), "palette.dark"),
        }
      : validatePalette(paletteInput, "palette");
  }
  const art = record(input.art ?? {}, "art");
  const safeArea = art.safeArea ?? "auto";
  if (!SAFE_AREAS.has(safeArea)) throw new Error("art.safeArea is invalid");
  const modes = record(input.modes ?? {}, "modes");
  const material = record(input.material ?? {}, "material");
  const materialStyle = material.style ?? "ambient";
  if (!MATERIAL_STYLES.has(materialStyle)) throw new Error("material.style must be ambient or studio");
  const background = input.background ?? null;
  if (background !== null && (typeof background !== "string" || background.length > 1000 || /[;{}]/.test(background))) {
    throw new Error("background must be a safe CSS image value");
  }
  return {
    schemaVersion: 1,
    id: input.id,
    name: input.name.trim(),
    description: description.trim(),
    image: safeRelativeImage(input.image),
    background,
    appearance,
    palette,
    art: {
      focusX: bounded(art.focusX, 0.5),
      focusY: bounded(art.focusY, 0.5),
      safeArea,
    },
    modes: {
      homeOpacity: bounded(modes.homeOpacity, 1),
      workOpacity: bounded(modes.workOpacity, 1),
      detailOpacity: bounded(modes.detailOpacity, 1),
      sidebarOpacity: bounded(modes.sidebarOpacity, 0.88),
    },
    material: {
      style: materialStyle,
      panelOpacity: bounded(material.panelOpacity, materialStyle === "studio" ? 0.82 : 0.9, 0.5, 1),
      cardOpacity: bounded(material.cardOpacity, materialStyle === "studio" ? 0.74 : 0.9, 0.45, 1),
      blur: bounded(material.blur, materialStyle === "studio" ? 24 : 18, 0, 40),
      radius: bounded(material.radius, materialStyle === "studio" ? 18 : 12, 6, 28),
      borderStrength: bounded(material.borderStrength, materialStyle === "studio" ? 0.14 : 0.12, 0, 0.7),
      shadowStrength: bounded(material.shadowStrength, materialStyle === "studio" ? 0.1 : 0.08, 0, 0.5),
    },
  };
}

function inside(root, candidate) {
  const path = relative(root, candidate);
  return path && path !== ".." && !path.startsWith(`..${sep}`) && !isAbsolute(path);
}

function dimensions(bytes, extension) {
  const u16be = (offset) => bytes[offset] * 256 + bytes[offset + 1];
  const u16le = (offset) => bytes[offset] + bytes[offset + 1] * 256;
  const u24le = (offset) => bytes[offset] + bytes[offset + 1] * 256 + bytes[offset + 2] * 65536;
  const u32be = (offset) => bytes[offset] * 0x1000000 + bytes[offset + 1] * 0x10000 + bytes[offset + 2] * 0x100 + bytes[offset + 3];
  const ascii = (offset, length) => String.fromCharCode(...bytes.subarray(offset, offset + length));
  if (extension === ".png" && bytes.length >= 24 && ascii(1, 3) === "PNG" && ascii(12, 4) === "IHDR") {
    return { width: u32be(16), height: u32be(20) };
  }
  if ((extension === ".jpg" || extension === ".jpeg") && bytes[0] === 0xff && bytes[1] === 0xd8) {
    const markers = new Set([0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf]);
    let offset = 2;
    while (offset + 9 < bytes.length) {
      if (bytes[offset] !== 0xff) { offset += 1; continue; }
      while (bytes[offset] === 0xff) offset += 1;
      const marker = bytes[offset++];
      if (marker === 0xda || marker === 0xd9) break;
      if (marker === 0x01 || (marker >= 0xd0 && marker <= 0xd8)) continue;
      const length = u16be(offset);
      if (length < 2 || offset + length > bytes.length) break;
      if (markers.has(marker)) return { width: u16be(offset + 5), height: u16be(offset + 3) };
      offset += length;
    }
  }
  if (extension === ".webp" && bytes.length >= 30 && ascii(0, 4) === "RIFF" && ascii(8, 4) === "WEBP") {
    const type = ascii(12, 4);
    if (type === "VP8X") return { width: u24le(24) + 1, height: u24le(27) + 1 };
    if (type === "VP8L" && bytes[20] === 0x2f) return {
      width: 1 + bytes[21] + ((bytes[22] & 0x3f) << 8),
      height: 1 + (bytes[22] >> 6) + (bytes[23] << 2) + ((bytes[24] & 0x0f) << 10),
    };
    if (type === "VP8 " && bytes[23] === 0x9d && bytes[24] === 0x01 && bytes[25] === 0x2a) {
      return { width: u16le(26) & 0x3fff, height: u16le(28) & 0x3fff };
    }
  }
  return null;
}

function validateImageBytes(bytes, extension) {
  const size = dimensions(bytes, extension);
  if (!size || !Number.isSafeInteger(size.width) || !Number.isSafeInteger(size.height)
    || size.width < 1 || size.height < 1 || size.width > MAX_DIMENSION || size.height > MAX_DIMENSION
    || size.width * size.height > MAX_PIXELS) {
    throw new Error("image dimensions are unsupported or exceed 50 megapixels");
  }
  return size;
}

export async function loadTheme(themeDir) {
  const root = resolve(themeDir);
  const manifest = validateTheme(JSON.parse(await readFile(join(root, "theme.json"), "utf8")));
  let imagePath = null;
  let imageDataUrl = null;
  let artKey = null;
  if (manifest.image) {
    imagePath = resolve(root, manifest.image);
    if (!inside(root, imagePath)) throw new Error("theme image escapes its directory");
    const [realRoot, realImage] = await Promise.all([realpath(root), realpath(imagePath)]);
    if (!inside(realRoot, realImage)) throw new Error("theme image symlink escapes its directory");
    const info = await stat(realImage);
    if (!info.isFile() || info.size < 1 || info.size > MAX_IMAGE_BYTES) throw new Error("theme image is empty or exceeds 15 MB");
    const extension = extname(realImage).toLowerCase();
    const bytes = await readFile(realImage);
    validateImageBytes(bytes, extension);
    artKey = createHash("sha256").update(bytes).digest("hex").slice(0, 24);
    const mime = extension === ".png" ? "image/png" : extension === ".webp" ? "image/webp" : "image/jpeg";
    imageDataUrl = `data:${mime};base64,${bytes.toString("base64")}`;
  }
  return { root, manifest, imagePath, imageDataUrl, artKey };
}

export async function listThemes(roots) {
  const found = [];
  for (const root of roots) {
    let entries;
    try { entries = await readdir(root, { withFileTypes: true }); }
    catch (error) { if (error.code === "ENOENT") continue; throw error; }
    for (const entry of entries) {
      if (!entry.isDirectory()) continue;
      try { found.push(await loadTheme(join(root, entry.name))); } catch { /* Ignore incomplete themes. */ }
    }
  }
  const unique = new Map();
  for (const theme of found) unique.set(theme.manifest.id, theme);
  return [...unique.values()].sort((a, b) => a.manifest.name.localeCompare(b.manifest.name));
}

export async function createTheme({ imagePath, name, storeRoot }) {
  if (typeof name !== "string" || !name.trim()) throw new Error("theme name is required");
  const extension = extname(imagePath).toLowerCase();
  if (!IMAGES.has(extension)) throw new Error("custom image must be PNG, JPEG, or WebP");
  const info = await stat(imagePath);
  if (!info.isFile() || info.size < 1 || info.size > MAX_IMAGE_BYTES) throw new Error("custom image is empty or exceeds 15 MB");
  validateImageBytes(await readFile(imagePath), extension);
  const slug = name.normalize("NFKD").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 32) || "custom";
  const digest = createHash("sha256").update(`${basename(imagePath)}\0${info.size}\0${info.mtimeMs}`).digest("hex").slice(0, 8);
  const id = `${slug}-${digest}`;
  const target = join(storeRoot, id);
  const temporary = `${target}.tmp-${process.pid}`;
  await mkdir(storeRoot, { recursive: true });
  await rm(temporary, { recursive: true, force: true });
  await mkdir(temporary, { recursive: true });
  const image = `background${extension}`;
  const manifest = validateTheme({
    schemaVersion: 1, id, name: name.trim(), image, appearance: "auto", palette: "auto",
    art: { safeArea: "auto", focusX: 0.5, focusY: 0.5 },
    material: { style: "studio" },
  });
  try {
    await copyFile(imagePath, join(temporary, image));
    await writeFile(join(temporary, "theme.json"), `${JSON.stringify(manifest, null, 2)}\n`);
    await rm(target, { recursive: true, force: true });
    await rename(temporary, target);
  } catch (error) {
    await rm(temporary, { recursive: true, force: true });
    throw error;
  }
  return loadTheme(target);
}

async function userThemeTarget(storeRoot, id) {
  if (typeof id !== "string" || !ID.test(id)) throw new Error("theme id must use lowercase letters, numbers, and hyphens");
  const root = resolve(storeRoot);
  const target = resolve(root, id);
  if (!inside(root, target)) throw new Error("user theme escapes its store");
  const [realRoot, realTarget] = await Promise.all([realpath(root), realpath(target)]);
  if (!inside(realRoot, realTarget) || realTarget !== resolve(realRoot, id)) throw new Error("user theme must be a real directory inside its store");
  const loaded = await loadTheme(target);
  if (loaded.manifest.id !== id) throw new Error("user theme directory and manifest id differ");
  return { target, loaded };
}

export async function renameUserTheme({ id, name, storeRoot }) {
  if (typeof name !== "string" || !name.trim()) throw new Error("theme name is required");
  const { target, loaded } = await userThemeTarget(storeRoot, id);
  const manifest = validateTheme({ ...loaded.manifest, name: name.trim() });
  const destination = join(target, "theme.json");
  const temporary = `${destination}.${process.pid}.tmp`;
  await writeFile(temporary, `${JSON.stringify(manifest, null, 2)}\n`, { mode: 0o600 });
  await rename(temporary, destination);
  return loadTheme(target);
}

export async function deleteUserTheme({ id, storeRoot, deletedRoot }) {
  const { target, loaded } = await userThemeTarget(storeRoot, id);
  await mkdir(deletedRoot, { recursive: true });
  const destination = join(deletedRoot, `${id}-${Date.now()}-${process.pid}`);
  await rename(target, destination);
  return { id: loaded.manifest.id, name: loaded.manifest.name, deleted: true, recoverableFrom: destination };
}
