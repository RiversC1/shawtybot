// Battle sound: real Pokémon cries (streamed from PokeAPI's public cries
// mirror) on switch-in, small synthesized SFX for hits/status/faints via the
// Web Audio API, and a battle theme from the poke-music S3 bucket
// (window.AUDIO_BASE_URL, set from the web app's AUDIO_BASE_URL env var),
// chosen by battle type. Themes loop like in the games: the intro plays
// once, then one section repeats seamlessly (decoded with Web Audio so the
// jump back is sample-accurate; a plain <audio loop> restarts the whole
// file with a gap, and is only the fallback if the decode fails).
window.BattleAudio = (function () {
  const CRY_BASE_LEGACY = "https://raw.githubusercontent.com/PokeAPI/cries/main/cries/pokemon/legacy/";
  const CRY_BASE_LATEST = "https://raw.githubusercontent.com/PokeAPI/cries/main/cries/pokemon/latest/";
  const AUDIO_BASE = window.AUDIO_BASE_URL || "https://poke-music.s3.us-east-1.amazonaws.com";
  const MUSIC_BY_TYPE = {
    gym: "gym_battle.mp3",
    custom_gym: "gym_battle.mp3",
    elite_four: "elite_battle.mp3",
    champion: "champion_battle.mp3",
    legend: "legend_battle.mp3",
  };
  // PvP and random-trainer battles keep the original general battle theme.
  const BATTLE_TRACK = MUSIC_BY_TYPE[window.BATTLE_TYPE] || "battle.mp3";
  const VICTORY_TRACK = "victory.mp3";
  // [loopStart, loopEnd] in seconds, measured from the files themselves:
  // the audio at loopEnd is the same music as at loopStart, so playback
  // jumps back there forever. Replacing a file in the bucket means
  // re-measuring its loop (an unknown file just loops whole).
  const LOOPS = {
    "battle.mp3": [22.25, 76.58751],
    "gym_battle.mp3": [56.5, 120.17499],
    "elite_battle.mp3": [27.45, 72.79263],
    "champion_battle.mp3": [5.05, 86.88451],
    "legend_battle.mp3": [15.6, 130.81435],
    "victory.mp3": [7.4, 14.78694],
  };
  // The last moments before loopEnd are blended into the music just before
  // loopStart, so the jump lands on exactly what was already playing.
  const SEAM_FADE = 0.15;

  let ctx = null;
  let sfxGain = null;
  let musicGain = null;
  let musicEl = null; // <audio> fallback, only if the Web Audio path fails

  // Some browsers throw on localStorage access entirely (strict cookie/site-
  // data blocking, private-mode edge cases) rather than just returning null —
  // never let that take down the mute button or the rest of the module.
  function readMutedPref() {
    try {
      return localStorage.getItem("sb_battle_muted") !== "0";
    } catch (e) {
      return true;
    }
  }

  function writeMutedPref(value) {
    try {
      localStorage.setItem("sb_battle_muted", value ? "1" : "0");
    } catch (e) {
      // No persistence this session — the in-memory `muted` flag still works.
    }
  }

  let muted = readMutedPref();

  // ---- volume mixer ----
  // Two user-facing levels (0-1): the battle theme, and effects (cries plus
  // synthesized hit/status/victory sounds). The default 0.7 reproduces the
  // levels these sounds always played at; each is scaled from its own base
  // so cries stay louder than the synth effects, as before.
  const DEFAULT_VOLUME = 0.7;
  const MUSIC_BASE = 0.5;   // 0.7 -> 0.35, the original theme volume
  const SFX_BASE = 0.5;     // 0.7 -> 0.35, the original synth volume
  const CRY_BASE = 0.785;   // 0.7 -> 0.55, the original cry volume

  function readVolume(key) {
    try {
      const raw = localStorage.getItem(key);
      const v = raw === null ? NaN : parseFloat(raw);
      return Number.isFinite(v) ? Math.min(1, Math.max(0, v)) : DEFAULT_VOLUME;
    } catch (e) {
      return DEFAULT_VOLUME;
    }
  }

  function writeVolume(key, value) {
    try {
      localStorage.setItem(key, String(value));
    } catch (e) {
      // Not persisted this session; the in-memory level still applies.
    }
  }

  let musicVolume = readVolume("sb_battle_vol_music");
  let effectsVolume = readVolume("sb_battle_vol_fx");

  function getVolumes() {
    return { music: musicVolume, effects: effectsVolume };
  }

  function setMusicVolume(v) {
    musicVolume = Math.min(1, Math.max(0, v));
    writeVolume("sb_battle_vol_music", musicVolume);
    if (musicEl) musicEl.volume = musicVolume * MUSIC_BASE;
    if (musicGain) musicGain.gain.value = musicVolume * MUSIC_BASE;
  }

  function setEffectsVolume(v) {
    effectsVolume = Math.min(1, Math.max(0, v));
    writeVolume("sb_battle_vol_fx", effectsVolume);
    if (sfxGain) sfxGain.gain.value = effectsVolume * SFX_BASE;
  }
  let wantMusic = false;

  function ensureCtx() {
    try {
      if (!ctx) {
        const AC = window.AudioContext || window.webkitAudioContext;
        if (!AC) return null;
        ctx = new AC();
        sfxGain = ctx.createGain();
        sfxGain.gain.value = effectsVolume * SFX_BASE;
        sfxGain.connect(ctx.destination);
        musicGain = ctx.createGain();
        musicGain.gain.value = musicVolume * MUSIC_BASE;
        musicGain.connect(ctx.destination);
        ctx.addEventListener("statechange", notifyState);
      }
      if (ctx.state === "suspended") ctx.resume().catch(() => {});
      return ctx;
    } catch (e) {
      return null;
    }
  }

  function isMuted() {
    return muted;
  }

  function setMuted(value) {
    muted = value;
    writeMutedPref(value);
    if (muted) {
      pauseMusic();
    } else if (wantMusic) {
      startMusic();
    }
    notifyState();
  }

  // ---- low-level synths ----

  function playTone(gainNode, freq, start, dur, type, peak) {
    const osc = ctx.createOscillator();
    const g = ctx.createGain();
    osc.type = type;
    osc.frequency.setValueAtTime(freq, start);
    g.gain.setValueAtTime(0, start);
    g.gain.linearRampToValueAtTime(peak, start + 0.012);
    g.gain.exponentialRampToValueAtTime(0.001, start + dur);
    osc.connect(g);
    g.connect(gainNode);
    osc.start(start);
    osc.stop(start + dur + 0.02);
  }

  function playGlide(gainNode, fromFreq, toFreq, start, dur, type, peak) {
    const osc = ctx.createOscillator();
    const g = ctx.createGain();
    osc.type = type;
    osc.frequency.setValueAtTime(fromFreq, start);
    osc.frequency.exponentialRampToValueAtTime(Math.max(20, toFreq), start + dur);
    g.gain.setValueAtTime(peak, start);
    g.gain.exponentialRampToValueAtTime(0.001, start + dur);
    osc.connect(g);
    g.connect(gainNode);
    osc.start(start);
    osc.stop(start + dur + 0.02);
  }

  function playNoiseBurst(gainNode, start, dur, peak, filterFreq) {
    const bufferSize = Math.ceil(ctx.sampleRate * dur);
    const buffer = ctx.createBuffer(1, bufferSize, ctx.sampleRate);
    const data = buffer.getChannelData(0);
    for (let i = 0; i < bufferSize; i++) data[i] = Math.random() * 2 - 1;
    const src = ctx.createBufferSource();
    src.buffer = buffer;
    const filter = ctx.createBiquadFilter();
    filter.type = "bandpass";
    filter.frequency.value = filterFreq || 1200;
    const g = ctx.createGain();
    g.gain.setValueAtTime(peak, start);
    g.gain.exponentialRampToValueAtTime(0.001, start + dur);
    src.connect(filter);
    filter.connect(g);
    g.connect(gainNode);
    src.start(start);
    src.stop(start + dur + 0.02);
  }

  function playArpeggio(freqs, start, stepDur, type, peak) {
    freqs.forEach((f, i) => playTone(sfxGain, f, start + i * stepDur, stepDur * 1.1, type, peak));
  }

  // ---- SFX ----

  function playSfx(name) {
    if (muted || !ensureCtx()) return;
    const now = ctx.currentTime + 0.01;
    switch (name) {
      case "hit":
        playNoiseBurst(sfxGain, now, 0.1, 0.5, 900);
        playTone(sfxGain, 160, now, 0.08, "square", 0.25);
        break;
      case "super_effective":
        playNoiseBurst(sfxGain, now, 0.12, 0.55, 1400);
        playArpeggio([320, 420], now, 0.06, "square", 0.3);
        break;
      case "not_very_effective":
        playNoiseBurst(sfxGain, now, 0.08, 0.3, 500);
        break;
      case "faint":
        playGlide(sfxGain, 480, 90, now, 0.55, "sawtooth", 0.35);
        break;
      case "status":
        playArpeggio([720, 520], now, 0.09, "triangle", 0.25);
        break;
      case "heal":
        playArpeggio([520, 660, 880], now, 0.09, "sine", 0.28);
        break;
      case "move":
        playTone(sfxGain, 440, now, 0.05, "square", 0.12);
        break;
      case "victory":
        playArpeggio([523, 659, 784, 1047], now, 0.13, "sine", 0.3);
        break;
      case "defeat":
        playArpeggio([392, 330, 262, 196], now, 0.18, "sawtooth", 0.28);
        break;
      default:
        break;
    }
  }

  function playCry(dexId) {
    if (muted || !dexId) return;
    ensureCtx();
    const legacy = new Audio(`${CRY_BASE_LEGACY}${dexId}.ogg`);
    legacy.volume = Math.min(1, effectsVolume * CRY_BASE);
    legacy.play().catch(() => {
      const latest = new Audio(`${CRY_BASE_LATEST}${dexId}.ogg`);
      latest.volume = Math.min(1, effectsVolume * CRY_BASE);
      latest.play().catch(() => {});
    });
  }

  // ---- background battle theme ----

  // Listeners told whenever what's audible may have changed (music started,
  // paused, or the mute preference flipped) — lets the page's sound button
  // reflect what the user actually hears, not just the saved preference.
  const stateListeners = [];
  function notifyState() {
    for (const cb of stateListeners) {
      try { cb(); } catch (e) { console.error(e); }
    }
  }
  function onStateChange(cb) {
    stateListeners.push(cb);
  }

  // Each theme is fetched and decoded once. Decoding happens off to the side
  // (an OfflineAudioContext needs no click and makes no sound), so a track
  // is ready to start the instant the browser allows sound. null means it
  // couldn't be decoded (network, CORS) and the <audio> fallback takes over.
  const trackCache = {};
  let useFallback = !(window.AudioContext || window.webkitAudioContext) || !window.OfflineAudioContext;
  let currentTrack = BATTLE_TRACK;
  let musicSrc = null; // { node, startedAt, offset, loop } while scheduled
  let resumeAt = 0;    // where the theme picks up again after a mute

  const wait = (ms) => new Promise((r) => setTimeout(r, ms));

  function loadTrack(name) {
    if (!trackCache[name]) {
      // The query string keeps the browser from reusing a copy it cached for
      // an <audio> element back when the bucket sent no CORS headers.
      trackCache[name] = fetch(`${AUDIO_BASE}/${name}?seamless=1`, { mode: "cors" })
        .then((r) => {
          if (!r.ok) throw new Error(`HTTP ${r.status}`);
          return r.arrayBuffer();
        })
        .then(decodeTrack)
        .then((buf) => {
          blendSeam(buf, LOOPS[name]);
          return buf;
        })
        .catch((e) => {
          console.warn(`Battle theme ${name} can't loop seamlessly (${e && e.message}), using <audio> instead.`);
          return null;
        });
    }
    return trackCache[name];
  }

  function decodeTrack(data) {
    const off = new OfflineAudioContext(2, 1, 44100);
    return new Promise((resolve, reject) => {
      const p = off.decodeAudioData(data, resolve, reject);
      if (p && p.catch) p.catch(reject);
    });
  }

  // Crossfades the last SEAM_FADE seconds before loopEnd into the music
  // just before loopStart, so whatever plays right before the jump is what
  // naturally leads into loopStart. Linear, since both sides are the same
  // music (an equal-power fade would bump the volume).
  function blendSeam(buf, loop) {
    if (!loop) return;
    const rate = buf.sampleRate;
    const start = Math.round(loop[0] * rate);
    const end = Math.round(loop[1] * rate);
    const n = Math.round(SEAM_FADE * rate);
    if (start < n || end > buf.length) return;
    for (let ch = 0; ch < buf.numberOfChannels; ch++) {
      const d = buf.getChannelData(ch);
      for (let i = 0; i < n; i++) {
        const w = (i + 0.5) / n;
        d[end - n + i] = d[end - n + i] * (1 - w) + d[start - n + i] * w;
      }
    }
  }

  // Where in the track the theme is right now (folding loops back in).
  function position() {
    if (!musicSrc) return resumeAt;
    const [ls, le] = musicSrc.loop;
    let p = musicSrc.offset + (ctx.currentTime - musicSrc.startedAt);
    if (le > ls && p >= le) p = ls + ((p - ls) % (le - ls));
    return p;
  }

  function stopSource() {
    if (!musicSrc) return;
    try {
      musicSrc.node.stop();
    } catch (e) {
      // Already stopped.
    }
    musicSrc.node.disconnect();
    musicSrc = null;
  }

  function ensureMusicEl() {
    if (!musicEl) {
      musicEl = new Audio();
      musicEl.loop = true;
      musicEl.volume = musicVolume * MUSIC_BASE;
      musicEl.preload = "auto";
      musicEl.addEventListener("playing", notifyState);
      musicEl.addEventListener("pause", notifyState);
    }
    return musicEl;
  }

  function playFallback() {
    const el = ensureMusicEl();
    const url = `${AUDIO_BASE}/${currentTrack}`;
    if (el.src !== url) el.src = url;
    if (el.paused) el.play().catch(notifyState);
  }

  // Makes the current theme play if it should. With the audio context still
  // waiting for a click, the theme is scheduled anyway and starts the moment
  // the browser allows sound.
  function playWanted() {
    if (muted || !wantMusic) return;
    if (useFallback) {
      playFallback();
      return;
    }
    const c = ensureCtx();
    if (!c) {
      useFallback = true;
      playFallback();
      return;
    }
    if (musicSrc) return;
    const name = currentTrack;
    loadTrack(name).then((buf) => {
      if (!buf) {
        useFallback = true;
        playWanted();
        return;
      }
      if (muted || !wantMusic || currentTrack !== name || musicSrc) return;
      const loop = LOOPS[name] && LOOPS[name][1] <= buf.duration ? LOOPS[name] : [0, buf.duration];
      const node = c.createBufferSource();
      node.buffer = buf;
      node.loop = true;
      node.loopStart = loop[0];
      node.loopEnd = loop[1];
      node.connect(musicGain);
      const offset = Math.min(resumeAt, loop[1] - 0.01);
      node.start(0, offset);
      musicSrc = { node, startedAt: c.currentTime, offset, loop };
      notifyState();
    });
  }

  function pauseMusic() {
    if (musicSrc) {
      resumeAt = position();
      stopSource();
    }
    if (musicEl) musicEl.pause();
    notifyState();
  }

  function startMusic() {
    wantMusic = true;
    playWanted();
    notifyState();
  }

  // Starts the theme right now and reports whether it's actually playing:
  // resolves false when muted or when the browser blocks it (no click on
  // this page yet), so the caller can ask for a click first. Waits (briefly)
  // for the track to finish loading, so it starts together with whatever
  // the caller shows next.
  async function startMusicNow() {
    wantMusic = true;
    notifyState();
    if (muted) return false;
    if (!useFallback) {
      const c = ensureCtx();
      const buf = c ? await Promise.race([loadTrack(currentTrack), wait(4000)]) : null;
      if (buf === null) useFallback = true;
    }
    if (useFallback) {
      const el = ensureMusicEl();
      el.src = `${AUDIO_BASE}/${currentTrack}`;
      return el.play().then(() => true, () => { notifyState(); return false; });
    }
    playWanted();
    if (ctx.state !== "running") await Promise.race([ctx.resume().catch(() => {}), wait(300)]);
    notifyState();
    return ctx.state === "running";
  }

  // Attempts to actually play the theme if it's supposed to be playing but
  // isn't (blocked pending a user gesture) — safe to call from any click,
  // since it never changes whether music is wanted, only whether it's
  // audibly caught up to that.
  function retryMusic() {
    if (muted || !wantMusic) return;
    playWanted();
  }

  // Swaps the battle theme for the looping victory theme (same volume
  // slider and mute button).
  function playVictoryMusic() {
    stopSource();
    if (musicEl) musicEl.pause();
    currentTrack = VICTORY_TRACK;
    resumeAt = 0;
    wantMusic = true;
    playWanted();
    notifyState();
  }

  function stopMusic() {
    wantMusic = false;
    stopSource();
    resumeAt = 0;
    if (musicEl) {
      musicEl.pause();
      musicEl.currentTime = 0;
    }
    notifyState();
  }

  function isMusicPlaying() {
    if (useFallback) return !!musicEl && !musicEl.paused;
    return !!musicSrc && !!ctx && ctx.state === "running";
  }

  // Download and decode the theme (then the victory theme) while the page
  // loads, so it's ready the moment it's needed.
  if (!muted && !useFallback) loadTrack(BATTLE_TRACK).then(() => loadTrack(VICTORY_TRACK));

  function handleEvent(event) {
    if (muted) return;
    switch (event.type) {
      case "switch_in":
        playCry(event.dex_id);
        break;
      case "damage":
      case "confusion_self_hit":
      case "status_damage":
      case "recoil":
        if (event.effectiveness === "super_effective") playSfx("super_effective");
        else if (event.effectiveness === "not_very_effective") playSfx("not_very_effective");
        else playSfx("hit");
        break;
      case "ability_damage":
        playSfx("hit");
        break;
      case "faint":
        playSfx("faint");
        break;
      case "status_applied":
        if (event.status && event.status !== "none") playSfx("status");
        break;
      case "heal":
      case "drain":
        playSfx("heal");
        break;
      case "move_used":
        playSfx("move");
        break;
      default:
        break;
    }
  }

  return {
    ensureCtx, isMuted, setMuted, startMusic, startMusicNow, stopMusic, retryMusic, isMusicPlaying, onStateChange, playVictoryMusic,
    getVolumes, setMusicVolume, setEffectsVolume,
    playSfx, playCry, handleEvent,
  };
})();
