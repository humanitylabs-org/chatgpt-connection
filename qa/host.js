"use strict";
let revision = 0, skins;
const frame = document.querySelector("iframe");
window.setSkin = async function (skin) {
  skins ||= (await (await fetch("skins.json")).json()).skins;
  const s = skins[skin];
  for (const [k,v] of Object.entries(s)) document.documentElement.style.setProperty("--"+k,v);
  document.documentElement.dataset.skin=skin;
  frame.contentWindow.postMessage({type:"wizard:appearance", appearance:{version:1,revision:++revision,skin,colorScheme:"dark",presentation:"window",tokens:{
    canvas:"transparent",base:s.bg,chrome:s["sk-win"],overlay:s["glass-2"],surface:`color-mix(in srgb, ${s.ink} 6%, transparent)`,"surface-raised":`color-mix(in srgb, ${s.ink} 10%, transparent)`,text:s.ink,"text-secondary":s["ink-2"],"text-muted":s["ink-3"],border:s["line-2"],accent:s["violet-2"],"accent-soft":`color-mix(in srgb, ${s["violet-2"]} 16%, transparent)`,"on-accent":s.bg,"reader-surface":s.bg
  }}},location.origin);
};
addEventListener("message", e=> {if(e.source===frame.contentWindow && e.data?.type==="wizard:appearance:ready") setSkin(document.documentElement.dataset.skin||"nebula");});
frame.addEventListener("load",()=>setSkin("nebula"));
