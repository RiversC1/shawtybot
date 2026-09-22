// Battle sound: real Pokémon cries (streamed from PokeAPI's public cries
// mirror) on switch-in, small synthesized SFX for hits/status/faints, and an
// original synthesized battle-theme loop — all generated in-browser via the
// Web Audio API, so there are no audio assets to host for the SFX/music.
window.BattleAudio = (function () {
  const CRY_BASE_LEGACY = "https://raw.githubusercontent.com/PokeAPI/cries/main/cries/pokemon/legacy/";
  const CRY_BASE_LATEST = "https://raw.githubusercontent.com/PokeAPI/cries/main/cries/pokemon/latest/";

  let ctx = null;
  let musicGain = null;
  let sfxGain = null;
  let muted = localStorage.getItem("sb_battle_muted") !== "0";
  let wantMusic = false;
  let musicPlaying = false;
  let musicTimer = null;
  let musicStep = 0;

  function ensureCtx() {
    if (!ctx) {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return null;
      ctx = new AC();
      musicGain = ctx.createGain();
      musicGain.gain.value = 0.16;
      musicGain.connect(ctx.destination);
      sfxGain = ctx.createGain();
      sfxGain.gain.value = 0.35;
      sfxGain.connect(ctx.destination);
    }
    if (ctx.state === "suspended") ctx.resume().catch(() => {});
    return ctx;
  }

  function isMuted() {
    return muted;
  }

  function setMuted(value) {
    muted = value;
    localStorage.setItem("sb_battle_muted", muted ? "1" : "0");
    if (muted) {
      stopMusic();
    } else if (ensureCtx() && wantMusic) {
      startMusic();
    }
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

  // ---- background battle theme: a short original chiptune loop, not a
  // copy of any game's actual soundtrack ----

  const BASS_RIFF = [82.41, 82.41, 98.0, 82.41, 73.42, 73.42, 65.41, 61.74];
  const STEP_SEC = 60 / 152 / 2;

  function scheduleStep() {
    if (!musicPlaying) return;
    const now = ctx.currentTime + 0.02;
    const freq = BASS_RIFF[musicStep % BASS_RIFF.length];
    playTone(musicGain, freq, now, STEP_SEC * 0.85, "sawtooth", 0.22);
    if (musicStep % 2 === 0) playNoiseBurst(musicGain, now, 0.03, 0.12, 6000);
    musicStep++;
    musicTimer = setTimeout(scheduleStep, STEP_SEC * 1000);
  }

  function startMusic() {
    wantMusic = true;
    if (muted || musicPlaying || !ensureCtx()) return;
    musicPlaying = true;
    musicStep = 0;
    scheduleStep();
  }

  function stopMusic() {
    wantMusic = false;
    musicPlaying = false;
    if (musicTimer) {
      clearTimeout(musicTimer);
      musicTimer = null;
    }
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

  return { ensureCtx, isMuted, setMuted, startMusic, stopMusic, playSfx, playCry, handleEvent };
})();
