// Battle sound: real Pokémon cries (streamed from PokeAPI's public cries
// mirror) on switch-in, small synthesized SFX for hits/status/faints via the
// Web Audio API, and a looping battle theme played from a locally hosted
// audio file in the poke-music S3 bucket (window.AUDIO_BASE_URL, set from
// the web app's AUDIO_BASE_URL env var), chosen by battle type.
window.BattleAudio = (function () {
  const CRY_BASE_LEGACY = "https://raw.githubusercontent.com/PokeAPI/cries/main/cries/pokemon/legacy/";
  const CRY_BASE_LATEST = "https://raw.githubusercontent.com/PokeAPI/cries/main/cries/pokemon/latest/";
  const AUDIO_BASE = window.AUDIO_BASE_URL || "https://poke-music.s3.us-east-1.amazonaws.com";
  const MUSIC_BY_TYPE = {
    gym: "gym_battle.mp3",
    elite_four: "elite_battle.mp3",
    champion: "champion_battle.mp3",
  };
  // PvP and random-trainer battles keep the original general battle theme.
  const MUSIC_URL = `${AUDIO_BASE}/${MUSIC_BY_TYPE[window.BATTLE_TYPE] || "battle.mp3"}`;

  let ctx = null;
  let sfxGain = null;
  let musicEl = null;

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
  let wantMusic = false;

  function ensureCtx() {
    try {
      if (!ctx) {
        const AC = window.AudioContext || window.webkitAudioContext;
        if (!AC) return null;
        ctx = new AC();
        sfxGain = ctx.createGain();
        sfxGain.gain.value = 0.35;
        sfxGain.connect(ctx.destination);
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
      if (musicEl) musicEl.pause();
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
    legacy.volume = 0.55;
    legacy.play().catch(() => {
      const latest = new Audio(`${CRY_BASE_LATEST}${dexId}.ogg`);
      latest.volume = 0.55;
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

  function ensureMusicEl() {
    if (!musicEl) {
      musicEl = new Audio(MUSIC_URL);
      musicEl.loop = true;
      musicEl.volume = 0.35;
      musicEl.preload = "auto";
      musicEl.addEventListener("playing", notifyState);
      musicEl.addEventListener("pause", notifyState);
    }
    return musicEl;
  }


  function startMusic() {
    wantMusic = true;
    retryMusic();
    notifyState();
  }

  // Attempts to actually play the theme if it's supposed to be playing but
  // isn't (blocked pending a user gesture) — safe to call from any click,
  // since it never changes whether music is wanted, only whether it's
  // audibly caught up to that.
  function retryMusic() {
    if (muted || !wantMusic) return;
    const el = ensureMusicEl();
    if (!el.paused) return;
    el.play().catch(notifyState);
  }

  function stopMusic() {
    wantMusic = false;
    if (musicEl) {
      musicEl.pause();
      musicEl.currentTime = 0;
    }
    notifyState();
  }

  function isMusicPlaying() {
    return !!musicEl && !musicEl.paused;
  }

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
    ensureCtx, isMuted, setMuted, startMusic, stopMusic, retryMusic, isMusicPlaying, onStateChange,
    playSfx, playCry, handleEvent,
  };
})();
