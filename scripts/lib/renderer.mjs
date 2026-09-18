import { HOST_ID, STATE_KEY, STYLE_ID, VERSION } from "./constants.mjs";

export function buildInstallExpression({ css, themes, activeId, force = true }) {
  const payload = JSON.stringify({ css, themes, activeId, force, STYLE_ID, HOST_ID, STATE_KEY, VERSION });
  return `(${installInRenderer.toString()})(${payload})`;
}

export function buildRemoveExpression() {
  return `(() => { const state = window[${JSON.stringify(STATE_KEY)}]; if (state?.cleanup) state.cleanup(); return true; })()`;
}

export function buildStatusExpression() {
  return `(() => { const s = window[${JSON.stringify(STATE_KEY)}]; if (!s) return { installed: false, pass: false }; const mode = document.documentElement.dataset.wbasMode; const nativeAppearance = document.documentElement.dataset.wbasNativeAppearance; const checks = { application: document.body?.getAttribute?.("data-application-name") === "workbuddy", root: Boolean(document.querySelector("#root")), sidebar: Boolean(document.querySelector('[data-view-id="sidebar"]')), main: Boolean(document.querySelector('[data-view-id="main-content"]')), menu: Boolean(document.getElementById(${JSON.stringify(HOST_ID)})), mode: ["home", "work", "detail"].includes(mode), nativeAppearance: ["light", "dark"].includes(nativeAppearance), horizontalOverflow: document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1 }; return { installed: true, pass: Object.values(checks).every(Boolean), version: s.version, themeId: s.themeId, mode, nativeAppearance, checks }; })()`;
}

async function installInRenderer(data) {
  const readMarkers = () => ({
    application: document.body?.getAttribute?.("data-application-name") === "workbuddy",
    root: Boolean(document.querySelector("#root")),
    shell: Boolean(document.querySelector(".teams-container")),
  });
  const deadline = Date.now() + 30_000;
  let markers = readMarkers();
  while (!Object.values(markers).every(Boolean) && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 100));
    markers = readMarkers();
  }
  if (!Object.values(markers).every(Boolean)) {
    const missing = Object.entries(markers).filter(([, present]) => !present).map(([name]) => name);
    throw new Error(`WorkBuddy DOM was not ready after 30s; missing markers: ${missing.join(", ")}`);
  }
  const html = document.documentElement;
  const body = document.body;
  const customStorageKey = "workbuddy-ambient-skin.custom-v2";
  const legacyCustomStorageKey = "workbuddy-ambient-skin.custom";
  const validCustom = (theme) => theme && typeof theme === "object" && /^user-[a-z0-9-]{1,80}$/.test(theme.id || "")
    && typeof theme.name === "string" && theme.name.trim() && /^data:image\/(png|jpeg|webp);base64,/.test(theme.imageDataUrl || "")
    && theme.imageDataUrl.length < 6_500_000;
  let customThemes = [];
  try {
    if (!data.force && localStorage.getItem("workbuddy-ambient-skin.paused") === "1") {
      return { installed: false, paused: true };
    }
    if (data.force) localStorage.removeItem("workbuddy-ambient-skin.paused");
    const saved = JSON.parse(localStorage.getItem(customStorageKey) || "[]");
    customThemes = (Array.isArray(saved) ? saved : []).filter(validCustom).slice(0, 8).map((theme) => ({ ...theme, localOnly: true }));
    const legacy = JSON.parse(localStorage.getItem(legacyCustomStorageKey) || "null");
    if (!customThemes.length && legacy?.id === "user-image" && /^data:image\/(png|jpeg|webp);base64,/.test(legacy.imageDataUrl || "") && legacy.imageDataUrl.length < 6_500_000) {
      customThemes = [{ ...legacy, id: "user-legacy", localOnly: true }];
      localStorage.setItem(customStorageKey, JSON.stringify(customThemes));
      localStorage.removeItem(legacyCustomStorageKey);
    }
    data.themes = data.themes.concat(customThemes);
  } catch {}
  window[data.STATE_KEY]?.cleanup?.();

  const style = document.createElement("style");
  style.id = data.STYLE_ID;
  style.textContent = data.css;
  document.head.appendChild(style);

  const rootVariables = ["--wbas-accent", "--wbas-secondary", "--wbas-surface", "--wbas-text", "--wbas-theme-surface", "--wbas-theme-text", "--wbas-light-accent", "--wbas-light-secondary", "--wbas-light-surface", "--wbas-light-text", "--wbas-dark-accent", "--wbas-dark-secondary", "--wbas-dark-surface", "--wbas-dark-text", "--wbas-focus-x", "--wbas-focus-y", "--wbas-home-opacity", "--wbas-work-opacity", "--wbas-detail-opacity", "--wbas-sidebar-opacity", "--wbas-panel-opacity", "--wbas-card-opacity", "--wbas-work-surface-opacity", "--wbas-detail-surface-opacity", "--wbas-protected-surface-opacity", "--wbas-material-blur", "--wbas-material-radius", "--wbas-border-strength", "--wbas-shadow-strength", "--wbas-background-image", "--wbas-bg-brightness", "--wbas-bg-saturation", "--wbas-video-active"];
  const cache = new Map();
  const analysisStorageKey = "workbuddy-ambient-skin.analysis-v4";
  try {
    localStorage.removeItem("workbuddy-ambient-skin.analysis-v1");
    localStorage.removeItem("workbuddy-ambient-skin.analysis-v2");
    localStorage.removeItem("workbuddy-ambient-skin.analysis-v3");
  } catch {}
  let disposed = false;
  let activeTheme = null;
  let timer = null;
  let appearanceTimer = null;
  let homeTextTimer = null;
  let editingThemeId = null;
  let deletingThemeId = null;

  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
  const tuningStorageKey = "workbuddy-ambient-skin.tuning-v1";
  let tunings = {};
  try {
    const saved = JSON.parse(localStorage.getItem(tuningStorageKey) || "{}");
    if (saved && typeof saved === "object" && !Array.isArray(saved)) tunings = saved;
  } catch {}
  const tuningDefaults = (theme) => ({
    wallpaper: 100,
    panel: Math.round((theme?.material?.panelOpacity ?? .84) * 100),
    blur: Math.round(theme?.material?.blur ?? 20),
    brightness: 100,
    saturation: 100,
  });
  const boundedTuning = (value, fallback, min, max) => {
    const numeric = Number(value);
    return Number.isFinite(numeric) ? clamp(numeric, min, max) : fallback;
  };
  const tuningFor = (theme) => {
    const defaults = tuningDefaults(theme);
    const saved = tunings[theme?.id] && typeof tunings[theme.id] === "object" ? tunings[theme.id] : {};
    return {
      wallpaper: boundedTuning(saved.wallpaper, defaults.wallpaper, 0, 100),
      panel: boundedTuning(saved.panel, defaults.panel, 72, 98),
      blur: boundedTuning(saved.blur, defaults.blur, 0, 32),
      brightness: boundedTuning(saved.brightness, defaults.brightness, 70, 120),
      saturation: boundedTuning(saved.saturation, defaults.saturation, 60, 140),
    };
  };
  const persistTunings = () => {
    try { localStorage.setItem(tuningStorageKey, JSON.stringify(tunings)); return true; }
    catch { return false; }
  };
  let paintTuner = () => {};
  const applyTuning = (theme) => {
    if (!theme) return;
    const tuning = tuningFor(theme);
    const wallpaperScale = tuning.wallpaper / 100;
    html.style.setProperty("--wbas-home-opacity", String(theme.modes.homeOpacity * wallpaperScale));
    const immersiveFloor = theme.modes.homeOpacity * .94;
    html.style.setProperty("--wbas-work-opacity", String(Math.max(theme.modes.workOpacity, immersiveFloor) * wallpaperScale));
    html.style.setProperty("--wbas-detail-opacity", String(Math.max(theme.modes.detailOpacity, immersiveFloor) * wallpaperScale));
    html.style.setProperty("--wbas-panel-opacity", `${tuning.panel}%`);
    html.style.setProperty("--wbas-card-opacity", `${Math.max(66, tuning.panel - 6)}%`);
    html.style.setProperty("--wbas-work-surface-opacity", `${clamp(tuning.panel - 66, 12, 22)}%`);
    html.style.setProperty("--wbas-detail-surface-opacity", `${clamp(tuning.panel - 60, 18, 28)}%`);
    html.style.setProperty("--wbas-protected-surface-opacity", `${clamp(tuning.panel + 5, 92, 97)}%`);
    html.style.setProperty("--wbas-material-blur", `${tuning.blur}px`);
    html.style.setProperty("--wbas-bg-brightness", `${tuning.brightness}%`);
    html.style.setProperty("--wbas-bg-saturation", `${tuning.saturation}%`);
  };
  const hex = (rgb) => `#${rgb.map((value) => clamp(Math.round(value), 0, 255).toString(16).padStart(2, "0")).join("")}`;
  const parseHex = (value, fallback = [120, 167, 255]) => {
    const match = /^#([0-9a-f]{6})$/i.exec(value || "");
    return match ? [0, 2, 4].map((offset) => Number.parseInt(match[1].slice(offset, offset + 2), 16)) : fallback;
  };
  const luminance = ([r, g, b]) => 0.2126 * r + 0.7152 * g + 0.0722 * b;
  const srgbLinear = (value) => { const v = value / 255; return v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4; };
  const linearSrgb = (value) => 255 * (value <= .0031308 ? 12.92 * value : 1.055 * Math.max(0, value) ** (1 / 2.4) - .055);
  const relativeLuminance = (rgb) => { const [r, g, b] = rgb.map(srgbLinear); return .2126 * r + .7152 * g + .0722 * b; };
  const contrastRatio = (foreground, background) => { const a = relativeLuminance(foreground), b = relativeLuminance(background); return (Math.max(a, b) + .05) / (Math.min(a, b) + .05); };
  const rgbToOklch = ([red, green, blue]) => {
    const [r, g, b] = [red, green, blue].map(srgbLinear);
    const l = .4122214708 * r + .5363325363 * g + .0514459929 * b;
    const m = .2119034982 * r + .6806995451 * g + .1073969566 * b;
    const s = .0883024619 * r + .2817188376 * g + .6299787005 * b;
    const lr = Math.cbrt(l), mr = Math.cbrt(m), sr = Math.cbrt(s);
    const L = .2104542553 * lr + .793617785 * mr - .0040720468 * sr;
    const a = 1.9779984951 * lr - 2.428592205 * mr + .4505937099 * sr;
    const bb = .0259040371 * lr + .7827717662 * mr - .808675766 * sr;
    return [L, Math.hypot(a, bb), (Math.atan2(bb, a) * 180 / Math.PI + 360) % 360];
  };
  const oklchToRgb = ([L, initialC, H]) => {
    const angle = H * Math.PI / 180;
    const convert = (C) => {
      const a = C * Math.cos(angle), b = C * Math.sin(angle);
      const lr = L + .3963377774 * a + .2158037573 * b;
      const mr = L - .1055613458 * a - .0638541728 * b;
      const sr = L - .0894841775 * a - 1.291485548 * b;
      const l = lr ** 3, m = mr ** 3, s = sr ** 3;
      return [
        4.0767416621 * l - 3.3077115913 * m + .2309699292 * s,
        -1.2684380046 * l + 2.6097574011 * m - .3413193965 * s,
        -.0041960863 * l - .7034186147 * m + 1.707614701 * s,
      ];
    };
    let C = initialC, linear = convert(C);
    for (let attempt = 0; attempt < 16 && linear.some((value) => value < 0 || value > 1); attempt += 1) { C *= .88; linear = convert(C); }
    return linear.map(linearSrgb);
  };
  const hueDistance = (a, b) => { const distance = Math.abs(a - b) % 360; return Math.min(distance, 360 - distance); };
  const ensureContrast = (color, background, minimum, direction) => {
    const adjusted = [...color];
    for (let attempt = 0; attempt < 24; attempt += 1) {
      if (contrastRatio(oklchToRgb(adjusted), background) >= minimum) break;
      adjusted[0] = clamp(adjusted[0] + direction * .025, .06, .96);
    }
    return oklchToRgb(adjusted);
  };
  const fallbackPalettes = {
    light: { accent: "#5676C8", secondary: "#7F65B8", surface: "#F7F8FB", text: "#20242C" },
    dark: { accent: "#8EAFFF", secondary: "#B49AF2", surface: "#11151F", text: "#F2F5FA" },
  };
  const paletteForAppearance = (primary, secondarySeed, dark) => {
    const surfaceColor = dark
      ? [.145, Math.min(primary[1] * .18, .032), primary[2]]
      : [.97, Math.min(primary[1] * .1, .018), primary[2]];
    const surface = oklchToRgb(surfaceColor);
    const accentColor = [
      clamp(primary[0], dark ? .68 : .46, dark ? .8 : .62),
      clamp(primary[1] * 1.04, .085, .19),
      primary[2],
    ];
    const secondaryColor = [
      clamp(secondarySeed[0], dark ? .68 : .48, dark ? .82 : .66),
      clamp(secondarySeed[1], .07, .17),
      secondarySeed[2],
    ];
    const accent = ensureContrast(accentColor, surface, 4.5, dark ? 1 : -1);
    const secondary = ensureContrast(secondaryColor, surface, 2.8, dark ? 1 : -1);
    const textSeed = dark ? [.94, .012, primary[2]] : [.22, .018, primary[2]];
    const text = ensureContrast(textSeed, surface, 7, dark ? 1 : -1);
    return { accent: hex(accent), secondary: hex(secondary), surface: hex(surface), text: hex(text) };
  };
  const generatedPalettes = (primary, secondarySeed) => ({
    light: paletteForAppearance(primary, secondarySeed, false),
    dark: paletteForAppearance(primary, secondarySeed, true),
  });
  const palettesForTheme = (theme, analysis) => {
    if (theme.palette === "auto") return analysis?.palettes || fallbackPalettes;
    if (theme.palette?.light && theme.palette?.dark) return theme.palette;
    const primary = rgbToOklch(parseHex(theme.palette?.accent));
    const secondary = rgbToOklch(parseHex(theme.palette?.secondary, [167, 139, 250]));
    const generated = generatedPalettes(primary, secondary);
    const declaredAppearance = theme.appearance === "auto"
      ? (relativeLuminance(parseHex(theme.palette?.surface, [17, 21, 31])) < .42 ? "dark" : "light")
      : theme.appearance;
    generated[declaredAppearance] = theme.palette;
    return generated;
  };
  const analysisKey = (theme) => theme.artKey || theme.id;
  const saveCustomThemes = (candidate) => {
    const unique = [...new Map(candidate.filter(validCustom).map((theme) => [theme.id, { ...theme, localOnly: true }])).values()].slice(0, 8);
    while (unique.length > 1 && JSON.stringify(unique).length > 7_000_000) unique.pop();
    try {
      localStorage.setItem(customStorageKey, JSON.stringify(unique));
      localStorage.removeItem(legacyCustomStorageKey);
      customThemes = unique;
      data.themes = data.themes.filter((theme) => !theme.localOnly).concat(customThemes);
      return true;
    } catch { return false; }
  };
  const removePersistedAnalysis = (theme) => {
    cache.delete(analysisKey(theme));
    try {
      const saved = JSON.parse(localStorage.getItem(analysisStorageKey) || "{}");
      delete saved[analysisKey(theme)];
      localStorage.setItem(analysisStorageKey, JSON.stringify(saved));
    } catch {}
  };
  const readPersistedAnalysis = (theme) => {
    if (theme.analysis?.algorithmVersion === 4 && theme.analysis?.palettes?.light && theme.analysis?.palettes?.dark) return theme.analysis;
    try {
      const saved = JSON.parse(localStorage.getItem(analysisStorageKey) || "{}");
      const analysis = saved[analysisKey(theme)];
      return analysis?.algorithmVersion === 4 && analysis?.palettes?.light && analysis?.palettes?.dark ? analysis : null;
    } catch { return null; }
  };
  const persistAnalysis = (theme, analysis) => {
    cache.set(analysisKey(theme), analysis);
    try {
      const saved = JSON.parse(localStorage.getItem(analysisStorageKey) || "{}");
      saved[analysisKey(theme)] = analysis;
      const trimmed = Object.fromEntries(Object.entries(saved).slice(-20));
      localStorage.setItem(analysisStorageKey, JSON.stringify(trimmed));
    } catch {}
  };

  const analyzeImage = (theme) => new Promise((resolve) => {
    if (!theme.imageDataUrl) return resolve(null);
    const persisted = readPersistedAnalysis(theme);
    if (persisted) return resolve(persisted);
    if (cache.has(analysisKey(theme))) return resolve(cache.get(analysisKey(theme)));
    const image = new Image();
    image.onload = () => {
      try {
        const width = 72;
        const height = Math.max(16, Math.round(width * image.naturalHeight / image.naturalWidth));
        const canvas = document.createElement("canvas");
        canvas.width = width; canvas.height = height;
        const context = canvas.getContext("2d", { willReadFrequently: true });
        context.drawImage(image, 0, 0, width, height);
        const pixels = context.getImageData(0, 0, width, height).data;
        const buckets = new Map();
        const lights = [];
        const perceptualLights = [];
        for (let index = 0; index < pixels.length; index += 4) {
          const rgb = [pixels[index], pixels[index + 1], pixels[index + 2]];
          const light = luminance(rgb); lights.push(light);
          const [L, C, H] = rgbToOklch(rgb); perceptualLights.push(L);
          if (C < .035 || L < .12 || L > .94) continue;
          const key = `${Math.round(H / 24) % 15}:${Math.round(L / .12)}:${Math.round(C / .055)}`;
          const bucket = buckets.get(key) || { weight: 0, L: 0, C: 0, sin: 0, cos: 0 };
          const weight = .45 + Math.min(C, .26) * 5;
          bucket.weight += weight; bucket.L += L * weight; bucket.C += C * weight;
          bucket.sin += Math.sin(H * Math.PI / 180) * weight; bucket.cos += Math.cos(H * Math.PI / 180) * weight;
          buckets.set(key, bucket);
        }
        perceptualLights.sort((a, b) => a - b);
        const medianLight = perceptualLights[Math.floor(perceptualLights.length / 2)] ?? .5;
        const dark = medianLight < .62;
        const ranked = [...buckets.values()].sort((a, b) => b.weight - a.weight).map((bucket) => ({
          weight: bucket.weight,
          color: [bucket.L / bucket.weight, bucket.C / bucket.weight, (Math.atan2(bucket.sin, bucket.cos) * 180 / Math.PI + 360) % 360],
        }));
        const primary = ranked[0]?.color || [.66, .14, 245];
        const contrasting = ranked.slice(1).filter((candidate) => hueDistance(candidate.color[2], primary[2]) >= 45)
          .sort((a, b) => (b.weight * (1 + Math.min(hueDistance(b.color[2], primary[2]), 120) / 240)) - (a.weight * (1 + Math.min(hueDistance(a.color[2], primary[2]), 120) / 240)))[0];
        const secondarySeed = contrasting?.color || [primary[0], Math.max(.07, primary[1] * .82), (primary[2] + 72) % 360];
        const zoneInformation = (start, end) => {
          let score = 0, count = 0;
          for (let y = 0; y < height; y += 1) for (let x = start; x < end; x += 1) {
            const current = lights[y * width + x];
            const previous = x > start ? lights[y * width + x - 1] : current;
            const above = y > 0 ? lights[(y - 1) * width + x] : current;
            score += Math.abs(current - previous) + Math.abs(current - above); count += 1;
          }
          return score / Math.max(1, count);
        };
        const zone = Math.round(width * .38);
        const left = zoneInformation(0, zone), right = zoneInformation(width - zone, width);
        const safeArea = Math.abs(left - right) < 2 ? "center" : left < right ? "left" : "right";
        const result = {
          algorithmVersion: 4,
          appearance: dark ? "dark" : "light",
          palettes: generatedPalettes(primary, secondarySeed),
          safeArea,
          focusX: safeArea === "left" ? .72 : safeArea === "right" ? .28 : .5,
        };
        persistAnalysis(theme, result); resolve(result);
      } catch { resolve(null); }
    };
    image.onerror = () => resolve(null);
    image.src = theme.imageDataUrl;
  });

  const applyTheme = async (theme) => {
    if (disposed || !theme) return false;
    setNotice(theme.imageDataUrl && theme.palette === "auto" ? "正在分析图片…" : "");
    const analysis = await analyzeImage(theme);
    if (disposed) return false;
    const palettes = palettesForTheme(theme, analysis);
    const appearance = theme.appearance === "auto" ? (analysis?.appearance || "dark") : theme.appearance;
    const safeArea = theme.art.safeArea === "auto" ? (analysis?.safeArea || "center") : theme.art.safeArea;
    const focusX = theme.art.safeArea === "auto" ? (analysis?.focusX ?? theme.art.focusX) : theme.art.focusX;
    const material = theme.material || { style: "studio", panelOpacity: .84, cardOpacity: .78, blur: 20, radius: 16, borderStrength: .14, shadowStrength: .1 };
    const variables = {
      "--wbas-light-accent": palettes.light.accent, "--wbas-light-secondary": palettes.light.secondary,
      "--wbas-light-surface": palettes.light.surface, "--wbas-light-text": palettes.light.text,
      "--wbas-dark-accent": palettes.dark.accent, "--wbas-dark-secondary": palettes.dark.secondary,
      "--wbas-dark-surface": palettes.dark.surface, "--wbas-dark-text": palettes.dark.text,
      "--wbas-focus-x": `${focusX * 100}%`, "--wbas-focus-y": `${theme.art.focusY * 100}%`,
      "--wbas-home-opacity": theme.modes.homeOpacity, "--wbas-work-opacity": theme.modes.workOpacity,
      "--wbas-detail-opacity": theme.modes.detailOpacity, "--wbas-sidebar-opacity": theme.modes.sidebarOpacity,
      "--wbas-panel-opacity": `${(material.panelOpacity ?? .82) * 100}%`,
      "--wbas-card-opacity": `${(material.cardOpacity ?? .74) * 100}%`,
      "--wbas-work-surface-opacity": `${clamp((material.panelOpacity ?? .82) * 100 - 66, 12, 22)}%`,
      "--wbas-detail-surface-opacity": `${clamp((material.panelOpacity ?? .82) * 100 - 60, 18, 28)}%`,
      "--wbas-protected-surface-opacity": `${clamp((material.panelOpacity ?? .82) * 100 + 5, 92, 97)}%`,
      "--wbas-material-blur": `${material.blur ?? 24}px`, "--wbas-material-radius": `${material.radius ?? 18}px`,
      "--wbas-border-strength": `${(material.borderStrength ?? .32) * 100}%`,
      "--wbas-shadow-strength": material.shadowStrength ?? .2,
      "--wbas-background-image": theme.imageDataUrl ? `url(${JSON.stringify(theme.imageDataUrl)})` : (theme.background || "none"),
    };
    for (const [name, value] of Object.entries(variables)) html.style.setProperty(name, String(value));
    applyTuning(theme);
    html.classList.add("workbuddy-ambient-skin");
    html.dataset.wbasAppearance = appearance;
    html.dataset.wbasSafe = safeArea;
    html.dataset.wbasTheme = theme.id;
    html.dataset.wbasMaterial = material.style || "studio";
    activeTheme = theme;
    host.dataset.appearance = html.dataset.wbasNativeAppearance || appearance;
    state.themeId = theme.id;
    try {
      localStorage.removeItem("workbuddy-ambient-skin.paused");
      localStorage.setItem("workbuddy-ambient-skin.active", theme.id);
      if (theme.localOnly && analysis) theme.analysis = analysis;
    } catch {}
    paintMenu(); paintTuner();
    setNotice(analysis ? "图片已分析并应用" : "");
    return true;
  };

  const syncMode = () => {
    if (disposed) return;
    const detail = document.querySelector('[data-view-id="detail-panel"]');
    const visibleDetail = detail && detail.getBoundingClientRect().width > 40;
    const welcome = document.querySelector(".main-content--welcome") || document.querySelector('[class*="emptyStateContainer"]');
    html.dataset.wbasMode = visibleDetail ? "detail" : welcome ? "home" : "work";
  };
  const scheduleMode = () => {
    clearTimeout(timer);
    timer = setTimeout(syncMode, 80);
  };
  const nativeAppearanceMedia = window.matchMedia?.("(prefers-color-scheme: dark)");
  const appearanceOf = (element) => {
    if (!element) return null;
    const attributes = [
      element.getAttribute?.("data-theme"),
      element.getAttribute?.("data-color-mode"),
      element.getAttribute?.("data-vscode-theme-kind"),
      element.getAttribute?.("data-vscode-theme-name"),
    ].filter(Boolean).join(" ").toLowerCase();
    const classes = String(element.className || "").toLowerCase();
    if (attributes.includes("ide night") || /(^|[^\w])(dark|cb-dark|vscode-dark|wb-theme--dark)([^\w]|$)/.test(`${attributes} ${classes}`)) return "dark";
    if (/(^|[^\w])(light|cb-light|vscode-light|wb-theme--light)([^\w]|$)/.test(`${attributes} ${classes}`)) return "light";
    return null;
  };
  const detectNativeAppearance = () => {
    const roots = [html, body, document.querySelector("#root"), document.querySelector(".teams-container")];
    for (const element of roots) {
      const appearance = appearanceOf(element);
      if (appearance) return appearance;
    }
    return nativeAppearanceMedia?.matches ? "dark" : "light";
  };
  const syncNativeAppearance = () => {
    if (disposed) return;
    const appearance = detectNativeAppearance();
    html.dataset.wbasNativeAppearance = appearance;
    if (typeof host !== "undefined") host.dataset.appearance = appearance;
  };
  const scheduleAppearance = () => {
    clearTimeout(appearanceTimer);
    appearanceTimer = setTimeout(syncNativeAppearance, 40);
  };
  // 首页文字是 React 渲染的 —— 它每次重渲染都会把我们改过的文案写回去，
  // 所以跟着 #root 的 MutationObserver 一起重新对齐。
  // 防抖 120ms：首页挂载时会连续产生几十次 mutation，不必每次都跑。
  const scheduleHomeText = () => {
    clearTimeout(homeTextTimer);
    homeTextTimer = setTimeout(() => { paintHomeText(); paintHomeTextUi(); }, 120);
  };
  const observer = new MutationObserver(() => { scheduleMode(); scheduleAppearance(); scheduleHomeText(); });
  observer.observe(document.querySelector("#root"), { childList: true, subtree: true });
  const appearanceObserver = new MutationObserver(scheduleAppearance);
  const appearanceAttributes = { attributes: true, attributeFilter: ["class", "data-theme", "data-color-mode", "data-vscode-theme-kind", "data-vscode-theme-name"] };
  appearanceObserver.observe(html, appearanceAttributes);
  appearanceObserver.observe(body, appearanceAttributes);
  const appearanceRoot = document.querySelector("#root");
  const appearanceShell = document.querySelector(".teams-container");
  if (appearanceRoot) appearanceObserver.observe(appearanceRoot, appearanceAttributes);
  if (appearanceShell && appearanceShell !== appearanceRoot) appearanceObserver.observe(appearanceShell, appearanceAttributes);
  window.addEventListener("resize", scheduleMode);
  nativeAppearanceMedia?.addEventListener?.("change", scheduleAppearance);

  // --- 视频背景层 ---------------------------------------------------------
  // 用一个真实的 <video> 铺在最底层（z-index:-2，和 #root::before 同层）。
  // ⚠️ 伪元素放不了 <video>，所以必须是真元素。
  // ⚠️ 只支持**本地 file:// 视频**：实测 file:///D:/xxx.mp4 可正常加载
  //    （readyState=4、像素可读），不受 CORS 限制，不需要改启动参数。
  //    但**必须用 Windows 盘符写法** D:/...，写成 Git Bash 的 /d/... 会
  //    报 MEDIA_ELEMENT_ERROR: Format error。
  const videoLayer = document.createElement("video");
  videoLayer.id = "wbas-video-layer";
  videoLayer.muted = true;               // 必须静音，否则浏览器拦截自动播放
  videoLayer.loop = true;
  videoLayer.autoplay = true;
  videoLayer.playsInline = true;
  videoLayer.preload = "auto";
  videoLayer.setAttribute("muted", "");
  videoLayer.setAttribute("playsinline", "");
  // disablePictureInPicture / controls 都不要：这是背景层，不是播放器
  videoLayer.disablePictureInPicture = true;
  videoLayer.disableRemotePlayback = true;
  videoLayer.controls = false;
  // ⚠️ 性能关键：把视频提升成独立合成层。
  //    不加的话每次重绘都要重新采样整屏视频帧，和 UI 抢主线程 → 拖动/滚动掉帧。
  //    translateZ(0) 让 GPU 单独拿一层，UI 更新不再等视频解码。
  //    ⚠️ 缩放**不能**写进这里的 transform —— 会被 paintVideo() 的整体赋值覆盖。
  //       缩放改用 CSS 变量 + 下面的 scale()，两者能共存。
  videoLayer.style.cssText = "position:fixed;inset:0;width:100%;height:100%;object-fit:cover;z-index:-2;pointer-events:none;opacity:0;transition:opacity 400ms ease;will-change:opacity,transform;transform:translateZ(0) scale(var(--wbas-video-scale,1));backface-visibility:hidden";
  const videoSettingsKey = "workbuddy-ambient-skin.video-v1";
  const videoNameKey = "workbuddy-ambient-skin.video-name-v1";
  // prompted：静音自动播放被拦只提示一次，否则每次 repaint 都刷 notice
  // stalled：正在缓冲（waiting 事件），用于状态行显示"缓冲中…"
  // blobName：blob: URL 的原始文件名（blob URL 重启后失效，只能靠名字提示）
  const videoState = { enabled: false, src: "", brightness: 100, blur: 0, scale: 100, prompted: false, stalled: false, blobName: "" };
  const readVideoSettings = () => {
    try {
      const saved = JSON.parse(localStorage.getItem(videoSettingsKey) || "null");
      if (saved && typeof saved === "object") {
        if (typeof saved.src === "string") videoState.src = saved.src;
        if (typeof saved.enabled === "boolean") videoState.enabled = saved.enabled;
        for (const key of ["brightness", "blur", "scale"]) {
          const numeric = Number(saved[key]);
          if (Number.isFinite(numeric)) videoState[key] = numeric;
        }
      }
      videoState.blobName = localStorage.getItem(videoNameKey) || "";
    } catch {}
    // ⚠️ blob: URL 只在**当前会话**有效（objectURL 存在内存里，页面一刷新就废）。
    //    重启后 localStorage 里剩一个死链，视频会一直"加载中"。
    //    所以恢复时看到 blob: 就当没设置，并保留文件名供 UI 提示用户重选。
    if (/^blob:/.test(videoState.src)) {
      videoState.src = "";
      videoState.enabled = false;
      videoState.expiredBlobName = videoState.blobName;
    }
    videoState.prompted = false;
    videoState.stalled = false;
  };
  const persistVideo = () => {
    try { localStorage.setItem(videoSettingsKey, JSON.stringify(videoState)); return true; }
    catch { return false; }
  };
  // 把 file:// 路径规范化成 Chromium 能吃的写法。
  // ⚠️ 路径里的中文/空格必须 **百分号编码**：Chromium 会把 `file:///D:/下载.mp4`
  //    解析成 `file:///D:/%E4%B8%8B%E8%BD%BD.mp4`，如果我们存的是未编码的原串，
  //    那么 paintVideo 里 `videoLayer.src !== videoState.src` 会**永远为真**，
  //    每次 repaint 都重新 load() 一遍 → 视频反复从头播。
  //    编码后两边一致，比较才有意义。
  // ⚠️ 必须**幂等**：先 decode 再 encode，否则 `%E4` 会被二次编码成 `%25E4`，
  //    用户来回点两次「应用」就把路径写坏了。decodeURIComponent 对没编码的
  //    原串（含中文）是安全的 no-op，所以「先解后编」两边都能吃。
  const encodeFilePath = (path) => {
    try {
      const decoded = decodeURIComponent(path);
      return decoded.split("/").map((segment) => encodeURIComponent(segment))
        // encodeURIComponent 会把 `:` 也编掉（D%3A），盘符需要还原
        .join("/").replace(/^([a-zA-Z])%3A/, "$1:");
    } catch { return path; }
  };
  const normalizeVideoSrc = (value) => {
    const text = String(value || "").trim();
    if (!text) return "";
    if (/^(data|blob):/i.test(text)) return text;
    if (/^file:/i.test(text)) {
      // 已经是 file:// —— 只对路径部分做编码（避免把 `file://` 自己的 `//` 编坏）
      return text.replace(/^file:(\/\/\/?)(.*)$/i, (_all, slashes, rest) => `file:${slashes}${encodeFilePath(rest)}`);
    }
    if (/^https?:/i.test(text)) return text;
    if (/^[a-zA-Z]:[\\/]/.test(text)) {                  // D:\a\b.mp4
      return "file:///" + encodeFilePath(text.replace(/\\/g, "/"));
    }
    if (/^\\\\/.test(text)) {                            // UNC \\srv\share\x.mp4
      return "file:" + encodeFilePath(text.replace(/\\/g, "/"));
    }
    return text.replace(/\\/g, "/");
  };
  const paintVideo = () => {
    if (disposed) return;
    const active = videoState.enabled && Boolean(videoState.src);
    if (active) {
      const nextSrc = videoState.src;
      if (videoLayer.getAttribute("data-wbas-src") !== nextSrc) {
        // 只在**真的换源**时才 load()。用 data-* 记上次的源而不是比 videoLayer.src：
        // 后者会被浏览器重写成绝对 URL（中文还会被百分号编码），跟我们的字符串
        // 未必逐字相同，容易误判成"换源了"→ 反复重载 → 卡顿。
        videoLayer.setAttribute("data-wbas-src", nextSrc);
        videoLayer.src = nextSrc;
        videoLayer.load();
      }
      if (videoLayer.paused) {
        const playing = videoLayer.play();
        if (playing && typeof playing.catch === "function") {
          playing.catch(() => {
            // 静音自动播放被拦：提示一次就好，别每次 repaint 都刷
            if (!videoState.prompted) { videoState.prompted = true; setNotice("播放被浏览器拦截，点一下页面即可恢复", true); }
          });
        }
      }
      // ⚠️ 只在值**真的变了**时才写 filter / 缩放。
      //    这两个每次重设都会触发合成器重建图层，滑块拖动或状态刷新时
      //    反复写会造成明显掉帧（"卡顿"的主因之一）。
      //    缩放走 CSS 变量（不能直接写 transform —— 会覆盖掉 translateZ(0) 那个
      //    图层提升，GPU 加速就没了）。
      const filter = `brightness(${videoState.brightness}%) blur(${videoState.blur}px)`;
      if (videoLayer.style.filter !== filter) videoLayer.style.filter = filter;
      const scale = String(videoState.scale / 100);
      if (videoLayer.style.getPropertyValue("--wbas-video-scale") !== scale) {
        videoLayer.style.setProperty("--wbas-video-scale", scale);
      }
    }
    // 视频在时把静态壁纸让出来，避免两层叠着互相干扰
    const flag = active ? "1" : "0";
    if (html.style.getPropertyValue("--wbas-video-active") !== flag) html.style.setProperty("--wbas-video-active", flag);
    const opacity = active ? "1" : "0";
    if (videoLayer.style.opacity !== opacity) videoLayer.style.opacity = opacity;
    const attr = active ? "on" : "off";
    if (html.dataset.wbasVideo !== attr) html.dataset.wbasVideo = attr;
  };
  const setVideo = (settings) => {
    if (settings && typeof settings === "object") {
      if ("src" in settings) {
        const next = normalizeVideoSrc(settings.src);
        // 换源要清掉"已提示过"的标记，新视频值得重新提示一次
        if (next !== videoState.src) videoState.prompted = false;
        videoState.src = next;
      }
      if ("enabled" in settings) videoState.enabled = Boolean(settings.enabled);
      for (const key of ["brightness", "blur", "scale"]) {
        if (key in settings) {
          const numeric = Number(settings[key]);
          if (Number.isFinite(numeric)) videoState[key] = numeric;
        }
      }
    }
    if (videoState.enabled && !videoState.src) videoState.enabled = false;
    persistVideo();
    paintVideo();
    paintVideoUi();
    paintMenu();
    return { enabled: videoState.enabled, src: videoState.src, brightness: videoState.brightness, blur: videoState.blur, scale: videoState.scale };
  };
  readVideoSettings();

  // --- 首页文字（可自定义 hero 文案）--------------------------------------
  // 改的是 WorkBuddy 首页的**显示文案**：hero 主标题、副标题、两个快捷按钮。
  // ⚠️ 首页是 React 渲染的，直接改 textContent 会被下一次重渲染冲掉，
  //    所以每次 MutationObserver 触发都要重新对齐一遍（paintHomeText）。
  //    写入前必须先比一遍当前值 —— 否则我们自己写 DOM 会再触发 observer，
  //    形成「写 → 触发 → 再写」的死循环。值相同就跳过，循环自然终止。
  // ⚠️ 空串语义是「用原生默认」，不是「显示空」。这样用户清空输入框
  //    就能回到官方文案，不需要额外记一份默认值（默认值可能随版本变）。
  const homeTextKey = "workbuddy-ambient-skin.home-text-v1";
  const homeTextState = { title: "", slogan: "", chipWork: "", chipCode: "" };
  let homeTextInputs = [];
  // 记下「我们覆盖掉的原生文案」，key 是元素本身。
  // 用 WeakMap 而不是 data-* 属性：不往 DOM 上挂用户的可见文字，元素被
  // React 换掉后也会自动回收。有了它，「清空输入框」能立刻还原原生文案，
  // 不必等 React 下次重渲染。
  const nativeHomeText = new WeakMap();
  const readHomeText = () => {
    try {
      const stored = JSON.parse(localStorage.getItem(homeTextKey) || "null");
      if (stored && typeof stored === "object") {
        for (const key of Object.keys(homeTextState)) {
          if (typeof stored[key] === "string") homeTextState[key] = stored[key].trim();
        }
      }
    } catch {}
  };
  const persistHomeText = () => {
    try { localStorage.setItem(homeTextKey, JSON.stringify(homeTextState)); return true; }
    catch { return false; }
  };
  // 首页 hero 的选择器（2026-09-17 实测）：
  //   h1.wb-home-header__title > span                    主标题
  //   .wb-home-header__slogan-slot-inner                 副标题槽位（原生留空）
  //   button.wb-scene-tabs__pill[data-show-id=...]       快捷按钮
  //     └ 子节点 = span.wb-scene-tabs__icon（图标）+ span（文字）
  // ⚠️ 按钮里那个文字 span **没有 class**，只能靠「不是 icon 的那个」来认。
  const HOME_TEXT_SELECTORS = {
    title: ".wb-home-header__title span",
    slogan: ".wb-home-header__slogan-slot-inner",
  };
  const HOME_CHIP_IDS = { chipWork: "home_mode_work", chipCode: "home_mode_code" };
  const chipLabelOf = (showId) => {
    const button = document.querySelector(`.wb-scene-tabs__pill[data-show-id="${showId}"]`);
    if (!button) return null;
    // 图标 span 有 class（wb-scene-tabs__icon），文字 span 没有 —— 取没有 class 的那个
    const spans = [...button.children].filter((child) => child.tagName === "SPAN");
    return spans.find((span) => !span.classList.contains("wb-scene-tabs__icon")) || null;
  };
  // 把某个元素对齐到目标文案；返回是否真的改动了 DOM。
  // ⚠️ 「值相同就跳过」不只是省事 —— 我们自己写 DOM 会再触发 MutationObserver，
  //    如果无条件写，就会变成「写 → 触发 → 再写」的死循环。
  const syncHomeTextNode = (element, key, custom) => {
    if (!element) return false;
    const current = element.textContent;
    if (custom) {
      if (current === custom) return false;
      // 第一次覆盖这个元素时，先把原生文案记下来
      if (element.dataset.wbasText !== key) {
        nativeHomeText.set(element, current);
        element.dataset.wbasText = key;
      }
      element.textContent = custom;
      return true;
    }
    // 目标为空 → 还原原生文案（只动我们改过的元素）
    if (element.dataset.wbasText !== key) return false;
    const original = nativeHomeText.has(element) ? nativeHomeText.get(element) : "";
    delete element.dataset.wbasText;
    if (current === original) return false;
    element.textContent = original;
    return true;
  };
  const paintHomeText = () => {
    if (disposed) return false;
    let touched = false;
    for (const key of Object.keys(HOME_TEXT_SELECTORS)) {
      touched = syncHomeTextNode(document.querySelector(HOME_TEXT_SELECTORS[key]), key, homeTextState[key]) || touched;
    }
    for (const [key, showId] of Object.entries(HOME_CHIP_IDS)) {
      touched = syncHomeTextNode(chipLabelOf(showId), key, homeTextState[key]) || touched;
    }
    return touched;
  };
  // 把当前值回灌到菜单输入框（用户正在打字的那一项不覆盖）
  const paintHomeTextUi = () => {
    for (const input of homeTextInputs) {
      const key = input.dataset.home;
      if (!key || document.activeElement === input) continue;
      const value = homeTextState[key] || "";
      if (input.value !== value) input.value = value;
      input.classList.toggle("is-set", Boolean(value));
    }
  };
  const setHomeText = (patch) => {
    if (patch && typeof patch === "object") {
      for (const key of Object.keys(homeTextState)) {
        if (key in patch && patch[key] !== null && patch[key] !== undefined) {
          homeTextState[key] = String(patch[key]).trim();
        }
      }
    }
    persistHomeText();
    paintHomeText();
    paintHomeTextUi();
    return { ...homeTextState };
  };
  readHomeText();

  const host = document.createElement("div");
  host.id = data.HOST_ID;
  host.style.cssText = "position:fixed;left:calc(100vw - 52px);top:52px;z-index:2147483000;pointer-events:auto";
  const shadow = host.attachShadow({ mode: "open" });
  shadow.innerHTML = `<style>
    :host{all:initial}button{font:13px/1.2 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#20242c}
    .trigger{position:relative;width:36px;height:36px;border-radius:50%;border:1px solid rgb(120 130 150/.28);background:rgb(250 251 253/.9);box-shadow:0 5px 18px rgb(0 0 0/.2);backdrop-filter:blur(14px);cursor:grab;font-size:17px;touch-action:none;user-select:none;transition:transform 160ms ease,box-shadow 180ms ease}
    .trigger::after{content:"";position:absolute;inset:6px;z-index:-1;border-radius:50%;background:color-mix(in srgb,var(--wbas-accent,#78a7ff) 68%,transparent);filter:blur(8px);opacity:0;transform:translate(var(--orb-trail-x,0),var(--orb-trail-y,0)) scale(.82);transition:opacity 160ms ease,transform 120ms ease}
    .trigger.dragging{cursor:grabbing;transform:scale(1.08);box-shadow:0 0 0 5px color-mix(in srgb,var(--wbas-accent,#78a7ff) 18%,transparent),0 10px 28px color-mix(in srgb,var(--wbas-accent,#78a7ff) 42%,transparent)}
    .trigger.dragging::after{opacity:.62}
    .panel{display:none;position:absolute;right:0;top:44px;width:260px;max-height:calc(100vh - 76px);overflow:auto;padding:7px;border:1px solid rgb(120 130 150/.22);border-radius:13px;background:rgb(250 251 253/.94);box-shadow:0 12px 34px rgb(0 0 0/.22);backdrop-filter:blur(18px)}
    :host([data-dock="left"]) .panel{left:0;right:auto}:host([data-vertical="bottom"]) .panel{top:auto;bottom:44px}
    :host([data-appearance="dark"]) button{color:#eef2f8}:host([data-appearance="dark"]) .trigger,:host([data-appearance="dark"]) .panel{background:rgb(25 29 38/.94);border-color:rgb(180 190 210/.2)}:host([data-appearance="dark"]) .title{color:#aeb7c7}:host([data-appearance="dark"]) .item:hover,:host([data-appearance="dark"]) .action:hover{background:rgb(235 240 255/.09)}
    .panel.open{display:block}.title{padding:7px 9px 5px;color:#687080;font:600 11px/1.2 -apple-system,sans-serif;text-transform:uppercase;letter-spacing:.06em}
    .row{display:flex;align-items:center;gap:2px;min-width:0}.row .item{flex:1;width:auto;min-width:0}.item{display:flex;width:100%;min-width:0;align-items:center;gap:9px;border:0;border-radius:8px;padding:8px 9px;background:transparent;cursor:pointer;text-align:left}.item>span:last-child{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.item:hover,.action:hover{background:rgb(20 30 50/.07)}
    .action{flex:0 0 26px;width:26px;height:28px;padding:0;border:0;border-radius:7px;background:transparent;color:#737b89;cursor:pointer;font-size:12px}
    .editor{display:grid;grid-template-columns:minmax(0,1fr) auto auto;gap:5px;padding:5px 4px 7px}.editor-input{min-width:0;height:30px;box-sizing:border-box;border:1px solid rgb(100 120 160/.3);border-radius:7px;padding:0 8px;background:rgb(255 255 255/.72);color:#20242c;font:13px/1.2 -apple-system,sans-serif;outline:none}.editor-input:focus{border-color:#68a5ef;box-shadow:0 0 0 2px rgb(104 165 239/.16)}
    .mini{height:30px;padding:0 8px;border:0;border-radius:7px;background:rgb(70 120 220/.13);cursor:pointer}.mini.danger{color:#c44858;background:rgb(196 72 88/.1)}.confirm{display:flex;align-items:center;gap:6px;padding:5px 4px 7px}.confirm-text{min-width:0;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#687080;font:12px/1.2 -apple-system,sans-serif}
    :host([data-appearance="dark"]) .editor-input{background:rgb(10 14 21/.58);border-color:rgb(180 190 210/.24);color:#eef2f8}:host([data-appearance="dark"]) .confirm-text{color:#aeb7c7}:host([data-appearance="dark"]) .mini{color:#eef2f8;background:rgb(120 160 240/.14)}:host([data-appearance="dark"]) .mini.danger{color:#ff8e9d;background:rgb(255 90 110/.1)}
    .item.active{background:rgb(80 125 230/.13);font-weight:650}.dot{width:10px;height:10px;border-radius:50%}.divider{height:1px;margin:5px 4px;background:rgb(100 110 130/.16)}
    .tuner{display:none;margin:3px 3px 7px;padding:9px;border-radius:10px;background:rgb(90 110 160/.07)}.tuner.open{display:block}.tuner-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:7px;color:#687080;font:600 11px/1.2 -apple-system,sans-serif}.tuner-reset{border:0;background:transparent;color:#5d74ac;cursor:pointer;font:11px/1.2 -apple-system,sans-serif}.control{display:grid;grid-template-columns:68px minmax(0,1fr) 38px;align-items:center;gap:7px;min-height:28px;color:#4f5868;font:12px/1.2 -apple-system,sans-serif}.control output{text-align:right;color:#697386;font-variant-numeric:tabular-nums}.control input{width:100%;min-width:0;background:transparent;accent-color:var(--wbas-accent,#6c83dd)}
    /* 26d) 滑块不能留原生白底。
       Chromium 的 input[type=range] 默认 appearance 会给一个不透明白色块
       （实测 computed background-color 就是 rgb(255,255,255)），
       在深色/半透明面板上就是一条突兀的白带。
       取消 appearance 后自己画轨道和滑块，才能跟着主题走。 */
    .control input[type="range"]{-webkit-appearance:none;appearance:none;height:16px;background:transparent;cursor:pointer}
    .control input[type="range"]::-webkit-slider-runnable-track{height:4px;border-radius:2px;background:color-mix(in srgb,var(--wbas-text,#20242c) 16%,transparent)}
    .control input[type="range"]::-webkit-slider-thumb{-webkit-appearance:none;appearance:none;width:13px;height:13px;margin-top:-4.5px;border:0;border-radius:50%;background:var(--wbas-accent,#6c83dd);box-shadow:0 1px 3px rgb(0 0 0/.24);transition:transform 120ms ease}
    .control input[type="range"]:hover::-webkit-slider-thumb{transform:scale(1.14)}
    .control input[type="range"]:active::-webkit-slider-thumb{transform:scale(1.22)}
    .control input[type="range"]:focus-visible::-webkit-slider-thumb{box-shadow:0 0 0 3px color-mix(in srgb,var(--wbas-accent,#6c83dd) 30%,transparent)}
    .control input[type="range"]::-moz-range-track{height:4px;border-radius:2px;background:color-mix(in srgb,var(--wbas-text,#20242c) 16%,transparent)}
    .control input[type="range"]::-moz-range-thumb{width:13px;height:13px;border:0;border-radius:50%;background:var(--wbas-accent,#6c83dd)}
    :host([data-appearance="dark"]) .control input[type="range"]::-webkit-slider-runnable-track{background:color-mix(in srgb,#f2f5fa 22%,transparent)}
    :host([data-appearance="dark"]) .control input[type="range"]::-moz-range-track{background:color-mix(in srgb,#f2f5fa 22%,transparent)}:host([data-appearance="dark"]) .tuner{background:rgb(225 232 255/.07)}:host([data-appearance="dark"]) .tuner-head,:host([data-appearance="dark"]) .control,:host([data-appearance="dark"]) .control output{color:#b9c2d3}:host([data-appearance="dark"]) .tuner-reset{color:#9db5ef}
    .video{margin:3px 3px 7px;padding:9px;border-radius:10px;background:rgb(90 110 160/.07)}.video-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:7px;color:#687080;font:600 11px/1.2 -apple-system,sans-serif}.video-toggle{position:relative;flex:0 0 auto;width:38px;height:21px;padding:0;border:0;border-radius:11px;background:rgb(120 132 152/.34);cursor:pointer;transition:background 160ms ease}.video-toggle::after{content:"";position:absolute;top:3px;left:3px;width:15px;height:15px;border-radius:50%;background:#fff;box-shadow:0 1px 3px rgb(0 0 0/.28);transition:transform 160ms ease}.video-toggle.on{background:var(--wbas-accent,#6c83dd)}.video-toggle.on::after{transform:translateX(17px)}
    .video-row{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:5px;margin-bottom:6px}.video-row[hidden]{display:none}.video-input{min-width:0;height:30px;box-sizing:border-box;border:1px solid rgb(100 120 160/.3);border-radius:7px;padding:0 8px;background:rgb(255 255 255/.72);color:#20242c;font:12px/1.2 -apple-system,sans-serif;outline:none}.video-input:focus{border-color:#68a5ef;box-shadow:0 0 0 2px rgb(104 165 239/.16)}.video-hint{padding:0 1px 6px;color:#7b8494;font:10.5px/1.35 -apple-system,sans-serif}.video-manual{padding:0 0 5px}.video-manual-toggle{border:0;background:transparent;color:#7b8494;cursor:pointer;font:10.5px/1.3 -apple-system,sans-serif;text-decoration:underline;text-underline-offset:2px;padding:0}.video-manual-toggle:hover{color:#5d74ac}
    /* 选中的视频：和「选择本地图片」同款行，右侧带 × 可清除 */
    .video-pick{width:100%}.video-pick.has-video{background:rgb(80 125 230/.13)}    .video-meta{display:flex;align-items:center;gap:6px;padding:1px 0 4px;color:#5d74ac;font:11px/1.2 -apple-system,sans-serif}.video-meta .badge{flex:0 0 auto;padding:1px 6px;border-radius:6px;background:rgb(70 120 220/.13);white-space:nowrap}.video-meta[data-state="error"]{color:#c44858}.video-meta[data-state="error"] .badge{background:rgb(196 72 88/.1)}.video-meta .path{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;direction:rtl;text-align:left}
    .video-clear{flex:0 0 22px;width:22px;height:20px;padding:0;border:0;border-radius:6px;background:transparent;color:#737b89;cursor:pointer;font-size:13px;line-height:1}.video-clear:hover{background:rgb(196 72 88/.12);color:#c44858}
    :host([data-appearance="dark"]) .video,:host([data-appearance="dark"]) .video-head{color:#b9c2d3}:host([data-appearance="dark"]) .video{background:rgb(225 232 255/.07)}:host([data-appearance="dark"]) .video-input{background:rgb(10 14 21/.58);border-color:rgb(180 190 210/.24);color:#eef2f8}:host([data-appearance="dark"]) .video-hint{color:#8d97a8}:host([data-appearance="dark"]) .video-meta{color:#9db5ef}:host([data-appearance="dark"]) .video-toggle{background:rgb(150 162 184/.32)}:host([data-appearance="dark"]) .video-meta .badge{background:rgb(120 160 240/.14)}:host([data-appearance="dark"]) .video-meta[data-state="error"]{color:#ff8e9d}:host([data-appearance="dark"]) .video-meta[data-state="error"] .badge{background:rgb(255 90 110/.12)}
    /* 27) 首页文字：改的是 WorkBuddy 首页 hero 的文案，不改功能 */
    .hometext{margin:3px 3px 7px;padding:9px;border-radius:10px;background:rgb(90 110 160/.07)}
    .hometext-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:7px;color:#687080;font:600 11px/1.2 -apple-system,sans-serif}
    .hometext-reset{border:0;background:transparent;color:#5d74ac;cursor:pointer;font:10.5px/1.3 -apple-system,sans-serif;padding:0;text-decoration:underline;text-underline-offset:2px}
    .hometext-reset:hover{color:#3f5f9e}
    .ht{display:grid;grid-template-columns:auto minmax(0,1fr);align-items:center;gap:7px;margin-bottom:5px;color:#687080;font:11px/1.2 -apple-system,sans-serif}
    .ht > span{white-space:nowrap}
    .ht-input{min-width:0;height:26px;box-sizing:border-box;border:1px solid rgb(100 120 160/.3);border-radius:7px;padding:0 7px;background:rgb(255 255 255/.72);color:#20242c;font:11.5px/1.2 -apple-system,sans-serif;outline:none}
    .ht-input::placeholder{color:#9aa2b1}
    .ht-input:focus{border-color:#68a5ef;box-shadow:0 0 0 2px rgb(104 165 239/.16)}
    .ht-input.is-set{background:rgb(80 125 230/.13);border-color:rgb(104 165 239/.5)}
    .hometext-hint{padding:2px 1px 0;color:#7b8494;font:10.5px/1.35 -apple-system,sans-serif}
    :host([data-appearance="dark"]) .hometext{background:rgb(225 232 255/.07)}
    :host([data-appearance="dark"]) .hometext-head,:host([data-appearance="dark"]) .ht{color:#b9c2d3}
    :host([data-appearance="dark"]) .hometext-reset{color:#9db5ef}
    :host([data-appearance="dark"]) .ht-input{background:rgb(10 14 21/.58);border-color:rgb(180 190 210/.24);color:#eef2f8}
    :host([data-appearance="dark"]) .ht-input::placeholder{color:#7f8899}
    :host([data-appearance="dark"]) .hometext-hint{color:#8d97a8}
    .notice{min-height:14px;padding:3px 9px 2px;color:#70798a;font:11px/1.25 -apple-system,sans-serif}.notice.error{color:#c44858}:host([data-appearance="dark"]) .notice{color:#aeb7c7}:host([data-appearance="dark"]) .notice.error{color:#ff8e9d}
    .share-overlay{display:none;position:fixed;inset:0;z-index:30;box-sizing:border-box;align-items:center;justify-content:center;padding:28px;background:rgb(8 12 22/.72);backdrop-filter:blur(18px) saturate(.8)}.share-overlay.open{display:flex}
    .share-dialog{width:min(980px,calc(100vw - 56px));max-height:calc(100vh - 56px);overflow:auto;box-sizing:border-box;padding:18px;border:1px solid rgb(255 255 255/.18);border-radius:20px;background:rgb(247 249 253/.97);box-shadow:0 28px 80px rgb(0 0 0/.42)}:host([data-appearance="dark"]) .share-dialog{background:rgb(24 28 37/.98)}
    .share-head{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:14px}.share-heading{font:700 16px/1.2 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#202632}:host([data-appearance="dark"]) .share-heading{color:#f0f3f8}.share-close{width:32px;height:32px;border:0;border-radius:50%;background:rgb(100 110 130/.1);cursor:pointer;font-size:18px}
    .share-layout{display:grid;grid-template-columns:minmax(0,1.7fr) minmax(220px,.72fr);gap:18px;align-items:start}.share-preview{display:block;width:100%;height:auto;border-radius:14px;background:#111827;box-shadow:0 14px 36px rgb(9 16 30/.22)}
    .share-form{display:grid;gap:12px}.share-field{display:grid;gap:6px;color:#596273;font:600 11px/1.2 -apple-system,sans-serif}.share-field input,.share-field textarea{box-sizing:border-box;width:100%;border:1px solid rgb(105 120 150/.25);border-radius:9px;padding:9px 10px;background:rgb(255 255 255/.74);color:#222936;font:13px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;outline:none;resize:none}.share-field input:focus,.share-field textarea:focus{border-color:var(--wbas-accent,#6c83dd);box-shadow:0 0 0 3px color-mix(in srgb,var(--wbas-accent,#6c83dd) 15%,transparent)}:host([data-appearance="dark"]) .share-field{color:#b7c0d0}:host([data-appearance="dark"]) .share-field input,:host([data-appearance="dark"]) .share-field textarea{background:rgb(8 12 19/.55);border-color:rgb(190 200 220/.2);color:#f0f3f8}
    .share-hint{color:#7b8494;font:11px/1.45 -apple-system,sans-serif}.share-save{height:40px;border:0;border-radius:10px;background:linear-gradient(135deg,var(--wbas-accent,#6c83dd),var(--wbas-secondary,#9d7bea));color:white!important;cursor:pointer;font-weight:700;box-shadow:0 8px 22px color-mix(in srgb,var(--wbas-accent,#6c83dd) 26%,transparent)}.share-save:disabled{opacity:.55;cursor:wait}
    @media (max-width:760px){.share-overlay{padding:14px}.share-dialog{width:calc(100vw - 28px);max-height:calc(100vh - 28px);padding:14px}.share-layout{grid-template-columns:1fr}.share-form{grid-template-columns:1fr 1fr}.share-field{grid-column:1/-1}.share-hint{grid-column:1/-1}}
    @media (prefers-reduced-motion:reduce){.trigger,.trigger::after{transition:none}.trigger.dragging{transform:none}.trigger.dragging::after{display:none}}
  </style><button class="trigger" title="WorkBuddy Ambient Skin">◐</button><div class="panel"><div class="title">Ambient Skin</div><div class="items"></div><div class="divider"></div><button class="item tune"><span class="dot" style="background:#9d7bea"></span><span>调整氛围</span></button><div class="tuner"><div class="tuner-head"><span>当前主题</span><button class="tuner-reset">恢复推荐值</button></div><label class="control"><span>壁纸强度</span><input type="range" data-tuning="wallpaper" min="0" max="100" step="1"><output></output></label><label class="control"><span>玻璃面板</span><input type="range" data-tuning="panel" min="72" max="98" step="1"><output></output></label><label class="control"><span>背景模糊</span><input type="range" data-tuning="blur" min="0" max="32" step="1"><output></output></label><label class="control"><span>背景亮度</span><input type="range" data-tuning="brightness" min="70" max="120" step="1"><output></output></label><label class="control"><span>背景饱和</span><input type="range" data-tuning="saturation" min="60" max="140" step="1"><output></output></label></div><button class="item share-open"><span class="dot" style="background:linear-gradient(135deg,#68a5ef,#9d7bea)"></span><span>生成主题收藏卡</span></button><button class="item upload"><span class="dot" style="background:#68a5ef"></span><span>＋ 选择本地图片</span></button><div class="divider"></div><div class="video"><div class="video-head"><span>视频背景</span><button class="video-toggle" role="switch" aria-checked="false" title="开关视频背景"></button></div><button class="item video-pick"><span class="dot" style="background:#7f65b8"></span><span>＋ 选择本地视频</span></button><div class="video-manual"><button class="video-manual-toggle" type="button">手动填路径</button></div><div class="video-row" hidden><input class="video-input editor-input" type="text" spellcheck="false" placeholder="D:/videos/bg.mp4" aria-label="本地视频路径"><button class="mini video-apply">应用</button></div><div class="video-hint">支持 mp4 / webm 等浏览器能解码的格式。视频会静音循环播放，不挡点击。</div><div class="video-meta video-state"></div><label class="control"><span>视频亮度</span><input type="range" data-video="brightness" min="30" max="150" step="1"><output></output></label><label class="control"><span>视频模糊</span><input type="range" data-video="blur" min="0" max="30" step="1"><output></output></label><label class="control"><span>视频缩放</span><input type="range" data-video="scale" min="100" max="160" step="1"><output></output></label></div><div class="hometext"><div class="hometext-head"><span>首页文字</span><button class="hometext-reset" type="button">恢复默认</button></div><label class="ht"><span>主标题</span><input class="ht-input" type="text" data-home="title" maxlength="40" spellcheck="false" placeholder="WorkBuddy, 我帮你" aria-label="首页主标题"></label><label class="ht"><span>副标题</span><input class="ht-input" type="text" data-home="slogan" maxlength="60" spellcheck="false" placeholder="留空则不显示" aria-label="首页副标题"></label><label class="ht"><span>按钮一</span><input class="ht-input" type="text" data-home="chipWork" maxlength="12" spellcheck="false" placeholder="日常办公" aria-label="第一个快捷按钮文字"></label><label class="ht"><span>按钮二</span><input class="ht-input" type="text" data-home="chipCode" maxlength="12" spellcheck="false" placeholder="代码开发" aria-label="第二个快捷按钮文字"></label><div class="hometext-hint">只改显示文字，按钮功能不受影响。留空即恢复原生文案。</div></div><button class="item native"><span class="dot" style="background:#9aa1ad"></span><span>原生界面</span></button><div class="notice"></div><input class="picker" type="file" accept="image/png,image/jpeg,image/webp" hidden><input class="video-picker" type="file" accept="video/mp4,video/webm,video/ogg,video/quicktime,video/x-matroska,.mp4,.webm,.ogv,.mov,.mkv,.m4v" hidden></div><div class="share-overlay" role="dialog" aria-modal="true" aria-label="生成主题收藏卡"><div class="share-dialog"><div class="share-head"><div class="share-heading">主题收藏卡</div><button class="share-close" title="关闭">×</button></div><div class="share-layout"><canvas class="share-preview" width="1600" height="900"></canvas><div class="share-form"><label class="share-field">标题<input class="share-title" maxlength="36"></label><label class="share-field">一句话描述<textarea class="share-description" rows="3" maxlength="72"></textarea></label><div class="share-hint">1600 × 900 PNG · 本地生成 · 不包含聊天内容</div><button class="share-save">保存 PNG</button></div></div></div></div>`;
  document.body.appendChild(host);
  const trigger = shadow.querySelector(".trigger");
  const panel = shadow.querySelector(".panel");
  const items = shadow.querySelector(".items");
  const picker = shadow.querySelector(".picker");
  const notice = shadow.querySelector(".notice");
  const tuner = shadow.querySelector(".tuner");
  const tune = shadow.querySelector(".tune");
  const tuningControls = [...shadow.querySelectorAll("[data-tuning]")];
  const shareOverlay = shadow.querySelector(".share-overlay");
  const shareCanvas = shadow.querySelector(".share-preview");
  const shareTitle = shadow.querySelector(".share-title");
  const shareDescription = shadow.querySelector(".share-description");
  const videoToggle = shadow.querySelector(".video-toggle");
  const videoInput = shadow.querySelector(".video-input");
  const videoApply = shadow.querySelector(".video-apply");
  const videoStateLabel = shadow.querySelector(".video-state");
  const videoControls = [...shadow.querySelectorAll("[data-video]")];
  const videoPick = shadow.querySelector(".video-pick");
  const videoPickLabel = videoPick?.lastElementChild;
  const videoPicker = shadow.querySelector(".video-picker");
  const videoManual = shadow.querySelector(".video-manual");
  const videoManualToggle = shadow.querySelector(".video-manual-toggle");
  const videoRow = shadow.querySelector(".video-row");
  const homeTextReset = shadow.querySelector(".hometext-reset");
  homeTextInputs = [...shadow.querySelectorAll("[data-home]")];
  const setNotice = (message, error = false) => {
    notice.textContent = message;
    notice.classList.toggle("error", error);
  };
  const orbPositionKey = "workbuddy-ambient-skin.orb-position-v1";
  const orbSize = 36;
  const orbMargin = 12;
  const setOrbPosition = (left, top, animate = false) => {
    host.style.transition = animate ? "left 280ms cubic-bezier(.2,.9,.25,1.15),top 180ms ease" : "none";
    host.style.left = `${clamp(left, orbMargin, Math.max(orbMargin, innerWidth - orbSize - orbMargin))}px`;
    host.style.top = `${clamp(top, orbMargin, Math.max(orbMargin, innerHeight - orbSize - orbMargin))}px`;
    const rect = host.getBoundingClientRect();
    host.dataset.dock = rect.left + orbSize / 2 < innerWidth / 2 ? "left" : "right";
    host.dataset.vertical = rect.top + orbSize / 2 > innerHeight * .58 ? "bottom" : "top";
  };
  const saveOrbPosition = () => {
    const rect = host.getBoundingClientRect();
    const range = Math.max(1, innerHeight - orbSize - orbMargin * 2);
    const position = { side: rect.left + orbSize / 2 < innerWidth / 2 ? "left" : "right", y: clamp((rect.top - orbMargin) / range, 0, 1) };
    try { localStorage.setItem(orbPositionKey, JSON.stringify(position)); } catch {}
    return position;
  };
  const loadOrbPosition = () => {
    let position = null;
    try { position = JSON.parse(localStorage.getItem(orbPositionKey) || "null"); } catch {}
    const side = position?.side === "left" ? "left" : "right";
    const y = Number.isFinite(position?.y) ? clamp(position.y, 0, 1) : clamp((52 - orbMargin) / Math.max(1, innerHeight - orbSize - orbMargin * 2), 0, 1);
    const left = side === "left" ? orbMargin : innerWidth - orbSize - orbMargin;
    setOrbPosition(left, orbMargin + y * Math.max(1, innerHeight - orbSize - orbMargin * 2));
  };
  let drag = null;
  let suppressOrbClick = false;
  const finishOrbDrag = (event) => {
    if (!drag || event.pointerId !== drag.pointerId) return;
    try { trigger.releasePointerCapture(event.pointerId); } catch {}
    if (drag.moved) {
      const rect = host.getBoundingClientRect();
      const left = rect.left + orbSize / 2 < innerWidth / 2 ? orbMargin : innerWidth - orbSize - orbMargin;
      setOrbPosition(left, rect.top, true);
      saveOrbPosition();
      suppressOrbClick = true;
      setTimeout(() => { suppressOrbClick = false; }, 0);
    }
    trigger.classList.remove("dragging");
    trigger.style.removeProperty("--orb-trail-x");
    trigger.style.removeProperty("--orb-trail-y");
    drag = null;
  };
  trigger.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    const rect = host.getBoundingClientRect();
    drag = { pointerId: event.pointerId, x: event.clientX, y: event.clientY, left: rect.left, top: rect.top, moved: false, lastX: event.clientX, lastY: event.clientY };
    trigger.setPointerCapture(event.pointerId);
  });
  trigger.addEventListener("pointermove", (event) => {
    if (!drag || event.pointerId !== drag.pointerId) return;
    const dx = event.clientX - drag.x;
    const dy = event.clientY - drag.y;
    if (!drag.moved && Math.hypot(dx, dy) < 4) return;
    drag.moved = true;
    panel.classList.remove("open");
    trigger.classList.add("dragging");
    setOrbPosition(drag.left + dx, drag.top + dy);
    trigger.style.setProperty("--orb-trail-x", `${clamp((drag.lastX - event.clientX) * 1.4, -14, 14)}px`);
    trigger.style.setProperty("--orb-trail-y", `${clamp((drag.lastY - event.clientY) * 1.4, -14, 14)}px`);
    drag.lastX = event.clientX; drag.lastY = event.clientY;
  });
  trigger.addEventListener("pointerup", finishOrbDrag);
  trigger.addEventListener("pointercancel", finishOrbDrag);
  trigger.addEventListener("click", () => {
    if (suppressOrbClick) return;
    panel.classList.toggle("open");
  });
  trigger.addEventListener("dblclick", () => {
    panel.classList.remove("open");
    setOrbPosition(innerWidth - orbSize - 16, 52, true);
    saveOrbPosition();
  });
  const syncOrbViewport = () => {
    const saved = saveOrbPosition();
    const left = saved.side === "left" ? orbMargin : innerWidth - orbSize - orbMargin;
    setOrbPosition(left, orbMargin + saved.y * Math.max(1, innerHeight - orbSize - orbMargin * 2));
  };
  window.addEventListener("resize", syncOrbViewport);
  loadOrbPosition();
  const tuningUnit = (key, value) => key === "blur" ? `${value}px` : `${value}%`;
  paintTuner = () => {
    if (!activeTheme) return;
    const tuning = tuningFor(activeTheme);
    for (const input of tuningControls) {
      const key = input.dataset.tuning;
      input.value = String(tuning[key]);
      input.nextElementSibling.textContent = tuningUnit(key, tuning[key]);
    }
  };
  tune.addEventListener("click", () => {
    tuner.classList.toggle("open");
    tune.classList.toggle("active", tuner.classList.contains("open"));
    paintTuner();
  });
  for (const input of tuningControls) {
    input.addEventListener("input", () => {
      if (!activeTheme) return;
      const key = input.dataset.tuning;
      const current = tuningFor(activeTheme);
      const value = Number(input.value);
      tunings[activeTheme.id] = { ...current, [key]: value };
      applyTuning(activeTheme);
      input.nextElementSibling.textContent = tuningUnit(key, value);
      const saved = persistTunings();
      setNotice(saved ? "氛围参数已保存" : "参数已应用，但无法保存", !saved);
    });
  }
  shadow.querySelector(".tuner-reset").addEventListener("click", () => {
    if (!activeTheme) return;
    delete tunings[activeTheme.id];
    const saved = persistTunings();
    applyTuning(activeTheme);
    paintTuner();
    setNotice(saved ? "已恢复主题推荐值" : "已恢复，但无法保存", !saved);
  });
  const activePresentation = async () => {
    const analysis = await analyzeImage(activeTheme);
    const palettes = palettesForTheme(activeTheme, analysis);
    const palette = palettes[html.dataset.wbasNativeAppearance === "dark" ? "dark" : "light"];
    const safeArea = activeTheme?.art?.safeArea === "auto" ? (analysis?.safeArea || "center") : activeTheme?.art?.safeArea;
    const focusX = activeTheme?.art?.safeArea === "auto" ? (analysis?.focusX ?? activeTheme?.art?.focusX ?? .5) : (activeTheme?.art?.focusX ?? .5);
    return { analysis, palette, safeArea, focusX };
  };
  const moodWords = (palette, appearance) => {
    const [r, g, b] = parseHex(palette.accent);
    const maximum = Math.max(r, g, b), minimum = Math.min(r, g, b);
    const saturation = maximum ? (maximum - minimum) / maximum : 0;
    let hue = 0;
    if (maximum !== minimum) {
      if (maximum === r) hue = 60 * (((g - b) / (maximum - minimum)) % 6);
      else if (maximum === g) hue = 60 * ((b - r) / (maximum - minimum) + 2);
      else hue = 60 * ((r - g) / (maximum - minimum) + 4);
    }
    hue = (hue + 360) % 360;
    const temperature = hue < 70 || hue >= 330 ? "温暖" : hue >= 170 && hue < 270 ? "清透" : hue >= 270 && hue < 330 ? "梦幻" : "自然";
    const energy = saturation > .58 ? "灵感" : appearance === "dark" ? "沉浸" : "安静";
    return [temperature, energy, appearance === "dark" ? "深色" : "浅色"];
  };
  const roundedPath = (context, x, y, width, height, radius) => {
    context.beginPath();
    context.roundRect(x, y, width, height, radius);
  };
  const fitTitle = (context, text, maxWidth) => {
    let size = 66;
    while (size > 42) {
      context.font = `700 ${size}px -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif`;
      if (context.measureText(text).width <= maxWidth) break;
      size -= 2;
    }
    return size;
  };
  const wrapLines = (context, text, maxWidth, maximum = 2) => {
    const characters = [...text.trim()];
    const lines = [];
    let current = "";
    for (const character of characters) {
      if (context.measureText(current + character).width <= maxWidth || !current) current += character;
      else { lines.push(current); current = character; if (lines.length === maximum - 1) break; }
    }
    const consumed = lines.join("").length;
    if (current && lines.length < maximum) lines.push(current + (consumed + current.length < characters.length ? "…" : ""));
    return lines;
  };
  const loadThemeImage = (theme) => new Promise((resolve) => {
    if (!theme?.imageDataUrl) return resolve(null);
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => resolve(null);
    image.src = theme.imageDataUrl;
  });
  let shareRenderToken = 0;
  const renderShareCard = async () => {
    if (!activeTheme) return false;
    const token = ++shareRenderToken;
    const [presentation, image] = await Promise.all([activePresentation(), loadThemeImage(activeTheme)]);
    if (token !== shareRenderToken || disposed) return false;
    const context = shareCanvas.getContext("2d");
    const width = shareCanvas.width, height = shareCanvas.height;
    const tuning = tuningFor(activeTheme);
    context.clearRect(0, 0, width, height);
    if (image) {
      const scale = Math.max(width / image.naturalWidth, height / image.naturalHeight);
      const sourceWidth = width / scale, sourceHeight = height / scale;
      const sourceX = clamp((image.naturalWidth - sourceWidth) * presentation.focusX, 0, image.naturalWidth - sourceWidth);
      const sourceY = clamp((image.naturalHeight - sourceHeight) * (activeTheme.art?.focusY ?? .5), 0, image.naturalHeight - sourceHeight);
      context.save();
      context.filter = `brightness(${tuning.brightness}%) saturate(${tuning.saturation}%)`;
      context.drawImage(image, sourceX, sourceY, sourceWidth, sourceHeight, 0, 0, width, height);
      context.restore();
    } else {
      const accent = presentation.palette.accent, secondary = presentation.palette.secondary;
      const base = context.createLinearGradient(0, 0, width, height);
      base.addColorStop(0, "#f8fbfc"); base.addColorStop(.52, "#eaf4f5"); base.addColorStop(1, "#edf0fa");
      context.fillStyle = base; context.fillRect(0, 0, width, height);
      const flare = context.createRadialGradient(width * .74, height * .25, 0, width * .74, height * .25, width * .5);
      flare.addColorStop(0, `${accent}aa`); flare.addColorStop(1, `${accent}00`);
      context.fillStyle = flare; context.fillRect(0, 0, width, height);
      const glow = context.createRadialGradient(width * .55, height * .86, 0, width * .55, height * .86, width * .52);
      glow.addColorStop(0, `${secondary}88`); glow.addColorStop(1, `${secondary}00`);
      context.fillStyle = glow; context.fillRect(0, 0, width, height);
    }
    const shade = context.createLinearGradient(0, 0, width, height);
    shade.addColorStop(0, "rgba(7,12,24,.16)"); shade.addColorStop(.52, "rgba(7,12,24,.02)"); shade.addColorStop(1, "rgba(7,12,24,.3)");
    context.fillStyle = shade; context.fillRect(0, 0, width, height);
    const accent = presentation.palette.accent;
    const secondary = presentation.palette.secondary;
    const cardOnRight = presentation.safeArea === "right";
    const cardX = cardOnRight ? 832 : 88, cardY = 474, cardWidth = 680, cardHeight = 330;
    context.save();
    context.shadowColor = "rgba(3,8,18,.32)"; context.shadowBlur = 42; context.shadowOffsetY = 18;
    roundedPath(context, cardX, cardY, cardWidth, cardHeight, 30);
    context.fillStyle = "rgba(12,18,32,.62)"; context.fill();
    context.restore();
    roundedPath(context, cardX, cardY, cardWidth, cardHeight, 30);
    context.strokeStyle = "rgba(255,255,255,.3)"; context.lineWidth = 2; context.stroke();
    const accentBar = context.createLinearGradient(cardX, cardY, cardX + 210, cardY);
    accentBar.addColorStop(0, accent); accentBar.addColorStop(1, secondary);
    roundedPath(context, cardX + 34, cardY + 34, 116, 7, 4);
    context.fillStyle = accentBar; context.fill();
    const title = (shareTitle.value.trim() || activeTheme.name).slice(0, 36);
    const description = (shareDescription.value.trim() || activeTheme.description || "把喜欢的画面，留在每天工作的地方。").slice(0, 72);
    const titleSize = fitTitle(context, title, cardWidth - 68);
    context.font = `700 ${titleSize}px -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif`;
    context.fillStyle = "#ffffff"; context.textBaseline = "top";
    context.fillText(title, cardX + 34, cardY + 64);
    context.font = '400 28px -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif';
    context.fillStyle = "rgba(255,255,255,.8)";
    wrapLines(context, description, cardWidth - 68, 2).forEach((line, index) => context.fillText(line, cardX + 36, cardY + 142 + index * 41));
    const appearance = activeTheme.appearance === "auto" ? (presentation.analysis?.appearance || "dark") : activeTheme.appearance;
    const words = moodWords(presentation.palette, appearance);
    context.font = '600 21px -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif';
    let tagX = cardX + 36;
    for (const word of words) {
      const tagWidth = context.measureText(word).width + 32;
      roundedPath(context, tagX, cardY + 247, tagWidth, 38, 19);
      context.fillStyle = "rgba(255,255,255,.12)"; context.fill();
      context.fillStyle = "rgba(255,255,255,.86)"; context.fillText(word, tagX + 16, cardY + 254);
      tagX += tagWidth + 10;
    }
    [accent, secondary, presentation.palette.surface, presentation.palette.text].forEach((color, index) => {
      context.beginPath(); context.arc(cardX + cardWidth - 40 - index * 25, cardY + 267, 8, 0, Math.PI * 2);
      context.fillStyle = color; context.fill();
      context.strokeStyle = "rgba(255,255,255,.5)"; context.lineWidth = 1.5; context.stroke();
    });
    context.font = '600 18px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif';
    context.fillStyle = "rgba(255,255,255,.78)";
    context.fillText("WORKBUDDY  /  AMBIENT SKIN", 88, 66);
    context.font = '500 17px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif';
    context.fillStyle = "rgba(255,255,255,.62)";
    context.textAlign = "right"; context.fillText("YOUR WORKSPACE, YOUR ATMOSPHERE", width - 88, 66); context.textAlign = "left";
    roundedPath(context, 36, 36, width - 72, height - 72, 26);
    context.strokeStyle = "rgba(255,255,255,.2)"; context.lineWidth = 2; context.stroke();
    return true;
  };
  let shareInputTimer = null;
  const scheduleShareRender = () => {
    clearTimeout(shareInputTimer);
    shareInputTimer = setTimeout(() => { renderShareCard(); }, 80);
  };
  const closeShare = () => {
    shareOverlay.classList.remove("open");
    shareRenderToken += 1;
  };
  const handleShareKey = (event) => {
    if (event.key === "Escape" && shareOverlay.classList.contains("open")) closeShare();
  };
  window.addEventListener("keydown", handleShareKey);
  shadow.querySelector(".share-open").addEventListener("click", async () => {
    if (!activeTheme) return;
    shareTitle.value = activeTheme.name;
    shareDescription.value = activeTheme.description || "把喜欢的画面，留在每天工作的地方。";
    panel.classList.remove("open");
    shareOverlay.classList.add("open");
    await renderShareCard();
  });
  shadow.querySelector(".share-close").addEventListener("click", closeShare);
  shareOverlay.addEventListener("click", (event) => { if (event.target === shareOverlay) closeShare(); });
  shareTitle.addEventListener("input", scheduleShareRender);
  shareDescription.addEventListener("input", scheduleShareRender);
  shadow.querySelector(".share-save").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true; button.textContent = "正在生成…";
    try {
      await renderShareCard();
      const blob = await new Promise((resolve) => shareCanvas.toBlob(resolve, "image/png"));
      if (!blob) throw new Error("PNG export failed");
      const link = document.createElement("a");
      const safeName = (shareTitle.value.trim() || activeTheme.name || "ambient-theme").replace(/[\\/:*?"<>|]/g, "-").slice(0, 48);
      const url = URL.createObjectURL(blob);
      link.href = url; link.download = `${safeName}-workbuddy-card.png`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 10_000);
      button.textContent = "已保存";
      setTimeout(() => { if (!disposed) button.textContent = "保存 PNG"; }, 1200);
    } catch {
      button.textContent = "生成失败";
      setTimeout(() => { if (!disposed) button.textContent = "保存 PNG"; }, 1500);
    } finally { button.disabled = false; }
  });
  shadow.querySelector(".native").addEventListener("click", () => {
    try { localStorage.setItem("workbuddy-ambient-skin.paused", "1"); } catch {}
    state.cleanup(true);
  });
  shadow.querySelector(".upload").addEventListener("click", () => picker.click());
  picker.addEventListener("change", () => {
    const file = picker.files?.[0];
    picker.value = "";
    if (!file) return;
    if (file.size < 1 || file.size > 15 * 1024 * 1024 || !/^image\/(png|jpeg|webp)$/.test(file.type)) {
      setNotice("请选择 15 MB 以内的 PNG、JPEG 或 WebP", true); return;
    }
    setNotice("正在压缩并分析图片…");
    const source = URL.createObjectURL(file);
    const image = new Image();
    image.onload = async () => {
      try {
        if (image.naturalWidth > 16384 || image.naturalHeight > 16384 || image.naturalWidth * image.naturalHeight > 50_000_000) {
          setNotice("图片尺寸超过 50MP 限制", true); return;
        }
        const scale = Math.min(1, 1600 / Math.max(image.naturalWidth, image.naturalHeight));
        const canvas = document.createElement("canvas");
        canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
        canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
        canvas.getContext("2d").drawImage(image, 0, 0, canvas.width, canvas.height);
        const custom = {
          schemaVersion: 1, id: `user-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`, name: (file.name.replace(/\.[^.]+$/, "") || "我的图片").slice(0, 60),
          description: "把喜欢的画面，留在每天工作的地方。",
          image: "uploaded.webp", imageDataUrl: canvas.toDataURL("image/webp", .82), background: null,
          appearance: "auto", palette: "auto",
          art: { focusX: .5, focusY: .5, safeArea: "auto" },
          modes: { homeOpacity: 1, workOpacity: 1, detailOpacity: 1, sidebarOpacity: .9 },
          material: { style: "studio", panelOpacity: .84, cardOpacity: .78, blur: 20, radius: 16, borderStrength: .14, shadowStrength: .1 },
          localOnly: true,
        };
        data.themes = data.themes.concat(custom);
        await applyTheme(custom);
        const persistent = saveCustomThemes([custom, ...customThemes]);
        paintMenu();
        setNotice(persistent ? `图片已保存（${customThemes.length}/8）` : "图片已应用，但空间不足，重启后不会保留", !persistent);
      } finally { URL.revokeObjectURL(source); }
    };
    image.onerror = () => { setNotice("图片读取失败", true); URL.revokeObjectURL(source); };
    image.src = source;
  });
  // ---- 视频背景 UI ----
  // paintVideoUi 由 setVideo() 与本地交互共同调用，负责把 videoState 回灌到控件。
  // ⚠️ 输入框只在**用户没在编辑**时覆盖，否则打字打到一半会被冲掉。
  // 路径在内部存的是编码后的 file:// URL，回填输入框时解回"用户当初敲的样子"。
  // 顺手把 `file:///` 前缀去掉 —— 用户填的是 `D:/videos/bg.mp4`，
  // 回填成 `file:///D:/videos/bg.mp4` 会让人以为要连协议一起写。
  const displayVideoSrc = (src) => {
    if (!src) return "";
    let text = src;
    try { text = decodeURIComponent(src); } catch {}
    // 只剥本地文件前缀；http(s)/data/blob 保持完整（那些协议是必须的）
    return text.replace(/^file:\/\/\/([a-zA-Z]:)/, "$1").replace(/^file:\/\/localhost\//, "");
  };
  // 状态行里显示的路径同理，但要保留 UNC 那种 file://server 的形态
  const displayVideoPath = (src) => {
    const text = displayVideoSrc(src);
    return text.replace(/^file:\/\//, "");
  };
  const paintVideoUi = () => {
    if (!videoToggle) return;
    videoToggle.classList.toggle("on", videoState.enabled);
    videoToggle.setAttribute("aria-checked", videoState.enabled ? "true" : "false");
    if (videoInput && document.activeElement !== videoInput) videoInput.value = displayVideoSrc(videoState.src);
    videoInput?.classList.toggle("error", false);
    for (const input of videoControls) {
      const key = input.dataset.video;
      input.value = String(videoState[key]);
      input.nextElementSibling.textContent = key === "blur" ? `${videoState[key]}px` : `${videoState[key]}%`;
    }
    // 「选择本地视频」行：选了视频就变成文件名 + 清除按钮，和图片主题同款手感
    if (videoPick && videoPickLabel) {
      const named = videoState.src ? videoFileName(videoState.src) : "";
      videoPick.classList.toggle("has-video", Boolean(named));
      videoPickLabel.textContent = named ? `已选：${named}` : "＋ 选择本地视频";
      videoPick.title = named ? displayVideoPath(videoState.src) : "选择本地视频文件";
      const existing = videoPick.parentElement.querySelector(".video-clear");
      if (named) {
        if (existing) { existing.hidden = false; } else {
          const clear = document.createElement("button");
          clear.className = "video-clear"; clear.type = "button"; clear.textContent = "×"; clear.title = "清除视频";
          clear.addEventListener("click", (event) => {
            event.stopPropagation();
            setVideo({ src: "", enabled: false });
            setNotice("已清除视频背景");
          });
          videoPick.after(clear);
        }
      } else if (existing) { existing.hidden = true; }
    }
    if (videoStateLabel) {
      if (!videoState.enabled) {
        // 关闭态清掉 error 标记，否则红色残留在下一次打开前一直挂着
        delete videoStateLabel.dataset.state;
        if (videoState.expiredBlobName) {
          videoStateLabel.dataset.state = "error";
          videoStateLabel.textContent = `「${videoState.expiredBlobName}」重启后需要重新选择`;
        } else {
          videoStateLabel.textContent = videoState.src ? "已关闭 · 点上方开关可恢复播放" : "未选择视频";
        }
      } else {
        // ⚠️ 只认 videoLayer.error 是真信号。不要用 readyState===0 判失败：
        //    刚 load() 的头几百毫秒它本来就是 0，会把"正在加载"误报成"读取失败"。
        const code = videoLayer.error ? videoLayer.error.code : null;
        const bad = code !== null;
        const badge = bad
          ? `无法解码（code ${code}）`
          : videoState.stalled ? "缓冲中…"
          : videoLayer.readyState >= 2 ? "播放中" : "正在加载…";
        videoStateLabel.dataset.state = bad ? "error" : "ready";
        videoStateLabel.innerHTML = `<span class="badge"></span><span class="path"></span>`;
        videoStateLabel.firstChild.textContent = badge;
        videoStateLabel.lastChild.textContent = videoFileName(videoState.src) || displayVideoPath(videoState.src);
      }
    }
  };
  // 从路径里取文件名（状态行空间小，显示文件名比全路径有用）。
  // blob: URL 取不到有意义的文件名，改用选择时记住的 blobName。
  function videoFileName(src) {
    if (/^blob:/.test(src)) return videoState.blobName || "本地视频";
    const text = displayVideoPath(src);
    const parts = text.split(/[\\/]/).filter(Boolean);
    return parts.length ? parts[parts.length - 1] : text;
  }
  // 没视频可开关时：点开关直接弹选择器
  function openVideoPicker() {
    if (videoPicker) videoPicker.click();
  }
  videoPick?.addEventListener("click", () => videoPicker.click());
  videoPicker?.addEventListener("change", () => {
    const file = videoPicker.files?.[0];
    videoPicker.value = "";   // 置空，否则重选同一个文件不触发 change
    if (!file) return;
    // ⚠️ 用 createObjectURL 而不是路径 —— 浏览器不给 <input type=file> 真实路径。
    //    blob: URL 指向本地文件，不复制内容、不占额外的磁盘。
    //    代价：重启后失效，所以我们要把文件名记住用于重新提示。
    if (file.size > 2 * 1024 * 1024 * 1024) { setNotice("视频超过 2 GB，建议换小一点的", true); return; }
    const url = URL.createObjectURL(file);
    videoState.blobName = file.name;
    delete videoState.expiredBlobName;
    try { localStorage.setItem(videoNameKey, file.name); } catch {}
    const applied = setVideo({ src: url, enabled: true });
    if (applied.enabled) {
      setNotice(`正在加载 ${file.name}…`);
    } else {
      setNotice("视频未能开启", true);
      URL.revokeObjectURL(url);
    }
  });
  videoManualToggle?.addEventListener("click", () => {
    if (!videoRow) return;
    const showing = !videoRow.hidden;
    videoRow.hidden = showing;
    videoManualToggle.textContent = showing ? "手动填路径" : "收起手动填路径";
    if (!showing && videoInput) videoInput.focus();
  });
  videoApply?.addEventListener("click", () => {
    const value = videoInput.value.trim();
    // 「应用」只负责换路径：开关状态维持原样（已开就继续开，没开就顺手开）。
    const applied = setVideo({ src: value, enabled: Boolean(value) });
    if (!value) { setNotice("已清除视频路径（视频背景已关闭）"); return; }
    setNotice(applied.enabled
      ? (/^blob:/.test(applied.src) ? "视频背景已应用" : "视频背景已应用（路径模式）")
      : "视频背景未能开启", !applied.enabled);
  });
  videoInput?.addEventListener("keydown", (event) => {
    if (event.key === "Enter") videoApply.click();
  });
  videoToggle?.addEventListener("click", () => {
    const next = !videoState.enabled;
    if (next && !videoState.src) {
      // 没选过视频就开：直接弹选择器，比让用户猜哪里点更顺
      setNotice("请先选择本地视频");
      videoPicker?.click();
      return;
    }
    const applied = setVideo({ src: videoState.src, enabled: next });
    setNotice(applied.enabled ? "视频背景已开启" : "视频背景已关闭", false);
    if (applied.enabled) {
      const playing = videoLayer.play();
      if (playing && typeof playing.catch === "function") {
        playing.catch(() => setNotice("播放被浏览器拦截，请点一下页面再试", true));
      }
    }
  });
  for (const input of videoControls) {
    input.addEventListener("input", () => {
      const key = input.dataset.video;
      const value = Number(input.value);
      setVideo({ [key]: value, ...(videoState.enabled ? {} : { enabled: false }) });
      input.nextElementSibling.textContent = key === "blur" ? `${value}px` : `${value}%`;
    });
  }
  // ---- 首页文字 UI ----
  // 边打边生效（不做防抖：改的只是几个文本节点，开销可以忽略，
  // 而且实时反馈比"点保存"直观得多）。
  for (const input of homeTextInputs) {
    input.addEventListener("input", () => {
      const key = input.dataset.home;
      if (!key) return;
      homeTextState[key] = input.value.trim();
      input.classList.toggle("is-set", Boolean(homeTextState[key]));
      persistHomeText();
      paintHomeText();
    });
    // 失焦时把「只输入了空格」这类情况收拾干净，并同步一次回灌
    input.addEventListener("blur", () => { setHomeText({ [input.dataset.home]: input.value }); });
  }
  homeTextReset?.addEventListener("click", () => {
    setHomeText({ title: "", slogan: "", chipWork: "", chipCode: "" });
    setNotice("首页文字已恢复原生文案");
  });
  // 视频元素自己的事件回灌到状态行。
  videoLayer.addEventListener("loadeddata", () => { videoState.stalled = false; paintVideoUi(); });
  videoLayer.addEventListener("playing", () => { videoState.stalled = false; paintVideoUi(); });
  videoLayer.addEventListener("waiting", () => { videoState.stalled = true; paintVideoUi(); });
  videoLayer.addEventListener("error", () => {
    paintVideoUi();
    if (!videoState.enabled) return;
    const code = videoLayer.error ? videoLayer.error.code : null;
    // MEDIA_ERR_SRC_NOT_SUPPORTED(4) 几乎都是"编解码器不支持"或"路径写法不对"
    setNotice(code === 4
      ? "视频无法解码：可能是格式不支持，或路径要写成 D:/videos/bg.mp4 这种盘符写法"
      : `视频加载失败（code ${code ?? "?"}）`, true);
  });
  // ---- 后台恢复：WorkBuddy 被最小化/切走时 Chromium 会节流甚至暂停视频，
  //      回来时主动 play() 一次，否则会停在那一帧（表现为"卡住了"）。----
  const resumeVideo = () => {
    if (!videoState.enabled || disposed) return;
    if (videoLayer.paused) {
      const playing = videoLayer.play();
      if (playing && typeof playing.catch === "function") playing.catch(() => {});
    }
  };
  document.addEventListener("visibilitychange", () => { if (!document.hidden) resumeVideo(); });
  window.addEventListener("focus", resumeVideo);
  // ---- 兜底守护：Chromium 在某些情况下（长时间后台、GPU 进程重启、系统休眠）
  //      会让视频进入 paused 但**不触发任何事件**，表现为画面停在某帧不动。
  //      低频轮询一次（5s）比听事件可靠，开销可忽略。
  //      ⚠️ 只在 enabled 且真的 paused 时才 play()，不做别的动作，
  //         避免和用户操作抢控制权。----
  const videoWatchdog = setInterval(() => {
    if (disposed || !videoState.enabled) return;
    if (videoLayer.paused && videoLayer.readyState >= 2) {
      const playing = videoLayer.play();
      if (playing && typeof playing.catch === "function") playing.catch(() => {});
    }
  }, 5000);
  const paintMenu = () => {
    items.textContent = "";
    for (const theme of data.themes) {
      const row = document.createElement("div");
      row.className = "row";
      const button = document.createElement("button");
      button.className = `item${theme.id === activeTheme?.id ? " active" : ""}`;
      button.title = theme.name;
      const accent = theme.palette === "auto"
        ? (theme.analysis?.palettes?.light?.accent || "#78A7FF")
        : (theme.palette.light?.accent || theme.palette.accent);
      button.innerHTML = `<span class="dot" style="background:${accent}"></span><span></span>`;
      button.lastChild.textContent = theme.name;
      button.addEventListener("click", () => { applyTheme(theme); panel.classList.remove("open"); });
      row.appendChild(button);
      if (theme.localOnly) {
        const rename = document.createElement("button");
        rename.className = "action"; rename.title = "重命名"; rename.textContent = "✎";
        rename.addEventListener("click", () => { editingThemeId = theme.id; deletingThemeId = null; paintMenu(); });
        const remove = document.createElement("button");
        remove.className = "action"; remove.title = "删除"; remove.textContent = "×";
        remove.addEventListener("click", () => { deletingThemeId = theme.id; editingThemeId = null; paintMenu(); });
        row.append(rename, remove);
      }
      items.appendChild(row);
      if (theme.localOnly && editingThemeId === theme.id) {
        const editor = document.createElement("div"); editor.className = "editor";
        const input = document.createElement("input"); input.className = "editor-input"; input.value = theme.name; input.maxLength = 60; input.setAttribute("aria-label", "皮肤名称");
        const save = document.createElement("button"); save.className = "mini"; save.textContent = "保存";
        const cancel = document.createElement("button"); cancel.className = "mini"; cancel.textContent = "取消";
        const commit = () => {
          const name = input.value.trim().slice(0, 60);
          if (!name) { setNotice("名称不能为空", true); input.focus(); return; }
          const updated = customThemes.map((candidate) => candidate.id === theme.id ? { ...candidate, name } : candidate);
          if (!saveCustomThemes(updated)) { setNotice("重命名保存失败：本地存储空间不足", true); return; }
          if (activeTheme?.id === theme.id) activeTheme = data.themes.find((candidate) => candidate.id === theme.id) || activeTheme;
          editingThemeId = null; paintMenu(); setNotice("已重命名");
        };
        save.addEventListener("click", commit); cancel.addEventListener("click", () => { editingThemeId = null; paintMenu(); });
        input.addEventListener("keydown", (event) => { if (event.key === "Enter") commit(); if (event.key === "Escape") { editingThemeId = null; paintMenu(); } });
        editor.append(input, save, cancel); items.appendChild(editor); queueMicrotask(() => { input.focus(); input.select(); });
      }
      if (theme.localOnly && deletingThemeId === theme.id) {
        const confirm = document.createElement("div"); confirm.className = "confirm";
        const label = document.createElement("span"); label.className = "confirm-text"; label.textContent = `删除“${theme.name}”？`;
        const yes = document.createElement("button"); yes.className = "mini danger"; yes.textContent = "删除";
        const no = document.createElement("button"); no.className = "mini"; no.textContent = "取消";
        yes.addEventListener("click", async () => {
          const remaining = customThemes.filter((candidate) => candidate.id !== theme.id);
          if (!saveCustomThemes(remaining)) { setNotice("删除保存失败：本地存储不可用", true); return; }
          removePersistedAnalysis(theme); deletingThemeId = null;
          if (activeTheme?.id === theme.id) await applyTheme(data.themes.find((candidate) => candidate.id === "paper-aurora") || data.themes[0]);
          else paintMenu();
          setNotice("已删除图片皮肤");
        });
        no.addEventListener("click", () => { deletingThemeId = null; paintMenu(); });
        confirm.append(label, yes, no); items.appendChild(confirm);
      }
    }
  };

  const state = {
    version: data.VERSION,
    themeId: null,
    // 供外部（CLI / 自检工具）读写视频背景设置
    setVideo,
    getVideo: () => ({ enabled: videoState.enabled, src: videoState.src, brightness: videoState.brightness, blur: videoState.blur, scale: videoState.scale, readyState: videoLayer.readyState, paused: videoLayer.paused, stalled: videoState.stalled, currentTime: videoLayer.currentTime, fileName: videoFileName(videoState.src), expiredBlobName: videoState.expiredBlobName || "", error: videoLayer.error ? videoLayer.error.code : null }),
    openVideoPicker,
    videoElement: videoLayer,
    // 供外部（CLI / 自检工具）读写首页文字
    setHomeText,
    getHomeText: () => ({ ...homeTextState }),
    paintHomeText,
    cleanup() {
      if (disposed) return;
      // ⚠️ 顺序要紧：先把首页文案还原，再置 disposed。
      //    paintHomeText 开头有 `if (disposed) return` 守卫，置位之后它就不干活了，
      //    两步写反会在停用皮肤后留下一句自定义文案，看着像 bug。
      setHomeText({ title: "", slogan: "", chipWork: "", chipCode: "" });
      try { localStorage.removeItem(homeTextKey); } catch {}
      disposed = true; clearTimeout(timer); clearTimeout(appearanceTimer); clearTimeout(shareInputTimer); clearTimeout(homeTextTimer); clearInterval(videoWatchdog); observer.disconnect(); appearanceObserver.disconnect(); window.removeEventListener("resize", scheduleMode); window.removeEventListener("resize", syncOrbViewport); window.removeEventListener("keydown", handleShareKey); window.removeEventListener("focus", resumeVideo); document.removeEventListener("visibilitychange", resumeVideo); nativeAppearanceMedia?.removeEventListener?.("change", scheduleAppearance);
      style.remove(); host.remove(); videoLayer.pause(); videoLayer.removeAttribute("src"); videoLayer.load(); videoLayer.remove(); html.classList.remove("workbuddy-ambient-skin");
      delete html.dataset.wbasAppearance; delete html.dataset.wbasNativeAppearance; delete html.dataset.wbasSafe; delete html.dataset.wbasTheme; delete html.dataset.wbasMode; delete html.dataset.wbasMaterial; delete html.dataset.wbasVideo;
      for (const name of rootVariables) html.style.removeProperty(name);
      delete window[data.STATE_KEY];
    },
  };
  window[data.STATE_KEY] = state;
  // 视频层挂到 #root 里（和 #root::before 同一个坐标系）；
  // #root 不存在时退回 body（上面已经等过 DOM 就绪，正常不会走到）。
  const videoParent = document.querySelector("#root") || document.body;
  videoParent.appendChild(videoLayer);
  paintVideo();
  paintVideoUi();
  paintHomeText(); paintHomeTextUi();
  paintMenu(); syncMode(); syncNativeAppearance();
  const stored = (() => { try { return localStorage.getItem("workbuddy-ambient-skin.active"); } catch { return null; } })();
  const selected = data.themes.find((theme) => theme.id === (data.activeId || stored)) || data.themes.find((theme) => theme.id === "paper-aurora") || data.themes[0];
  return applyTheme(selected).then(() => ({ installed: true, version: data.VERSION, themeId: state.themeId, mode: html.dataset.wbasMode }));
}
