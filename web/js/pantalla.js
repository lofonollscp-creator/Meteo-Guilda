// Zona segura en iPhone/iPad (isla dinámica, muesca): normalmente la da el sistema con env(safe-area-inset-top). Si la app está instalada, la página
// ocupa TODA la pantalla (la barra de estado se superpone) y el sistema dice 0, se usa una estimada por modelo para que nada quede bajo la barra.
const ua = navigator.userAgent;
const ios = /iphone|ipad|ipod/i.test(ua) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
const standalone = () => matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
let probe, last = { env: 0, extra: 0, why: "no iOS" };

function measure() {
  probe ||= (() => { const d = document.createElement("div"); d.style.cssText = "position:fixed;top:0;left:0;width:0;height:0;visibility:hidden;pointer-events:none;padding-top:env(safe-area-inset-top,0px)"; document.body.appendChild(d); return d; })();
  const env = parseFloat(getComputedStyle(probe).paddingTop) || 0;
  let extra = 0, why = ios ? (standalone() ? "el sistema la informa" : "navegador (Safari gestiona la barra)") : "no es iOS";
  if (ios && standalone() && env < 1) {
    const long = Math.max(screen.width, screen.height), short = Math.min(screen.width, screen.height), portrait = innerHeight >= innerWidth;
    const overlaps = innerHeight >= (portrait ? long : short) - 2;          // la página llega hasta arriba del todo
    if (overlaps && portrait) { extra = short >= 700 ? 24 : [852, 874, 932, 956].includes(long) ? 59 : [812, 844, 896, 926].includes(long) ? 47 : 20; why = "estimada (el sistema informa 0 y la página llega arriba)"; }
    else why = overlaps ? "horizontal: sin barra de estado" : "la página empieza bajo la barra";
  }
  last = { env: Math.round(env), extra, why };
  document.documentElement.style.setProperty("--sat-extra", extra + "px");
}
export function initPantalla() { measure(); addEventListener("resize", measure); addEventListener("orientationchange", () => setTimeout(measure, 300)); }
/** Texto de diagnóstico (Ajustes → Cuenta) para saber qué ve exactamente el dispositivo. */
export function infoPantalla() {
  measure();
  return `Ventana ${innerWidth}×${innerHeight} · pantalla ${screen.width}×${screen.height} · ${standalone() ? "modo app" : "navegador"} · zona segura superior: sistema ${last.env} px, aplicada ${Math.max(last.env, last.extra)} px (${last.why})`;
}
