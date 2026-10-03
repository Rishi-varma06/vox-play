/* VoxPlay chess studio -----------------------------------------------------
 *
 * Four screens: upload, progress, review, audio. The server does the work and
 * the browser polls it, because analysis takes about half a minute and a
 * request that long looks like a hang.
 *
 * The review screen is the reason this app exists rather than a CLI flag.
 * Nothing is spoken until a person has read the text, and whatever they edit
 * goes back through the same verifier the pipeline used -- so a reviewer can
 * type an invented tactic into a beat and watch it get flagged.
 * -------------------------------------------------------------------------- */

'use strict';

const SAMPLE_PGN = `[Event "Paris Opera"]
[Site "Paris FRA"]
[Date "1858.11.02"]
[Round "-"]
[White "Paul Morphy"]
[Black "Duke Karl and Count Isouard"]
[Result "1-0"]
[ECO "C41"]
[Opening "Philidor Defense"]
[Termination "Normal"]

1. e4 e5 2. Nf3 d6 3. d4 Bg4 4. dxe5 Bxf3 5. Qxf3 dxe5 6. Bc4 Nf6 7. Qb3 Qe7
8. Nc3 c6 9. Bg5 b5 10. Nxb5 cxb5 11. Bxb5+ Nbd7 12. O-O-O Rd8 13. Rxd7 Rxd7
14. Rd1 Qe6 15. Bxd7+ Nxd7 16. Qb8+ Nxb8 17. Rd8# 1-0`;

const PIECES = { k: '♚', q: '♛', r: '♜', b: '♝', n: '♞', p: '♟' };

const state = {
  jobId: null,
  snap: null,
  poll: null,
  recheckTimer: null,
  movesByPly: {},
};

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
};

/* ---------------------------------------------------------------- screens */

function showStep(n) {
  $('panel-upload').classList.toggle('hidden', n !== 1);
  $('panel-progress').classList.toggle('hidden', n !== 2);
  $('panel-review').classList.toggle('hidden', n !== 3 && n !== 4);
  $('panel-audio').classList.toggle('hidden', n !== 4);
  document.querySelectorAll('#stepper .step').forEach((s) => {
    const step = Number(s.dataset.step);
    s.classList.toggle('is-active', step === n);
    s.classList.toggle('is-done', step < n);
  });
  // A textarea measured while its panel is display:none reports a
  // scrollHeight of 0, so every beat would open collapsed with a scrollbar.
  // Size them once the panel is actually on screen.
  if (n === 3 || n === 4) growAll();
  if (n === 4) $('panel-audio').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function growAll() {
  document.querySelectorAll('#beats .beat textarea').forEach(autoGrow);
}

/* ------------------------------------------------------------ upload form */

document.querySelectorAll('.tab').forEach((tab) => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach((t) => t.classList.remove('is-active'));
    document.querySelectorAll('.tabpane').forEach((p) => p.classList.remove('is-active'));
    tab.classList.add('is-active');
    document.querySelector(`.tabpane[data-pane="${tab.dataset.tab}"]`).classList.add('is-active');
  });
});

const dropzone = $('dropzone');
const fileInput = $('fileInput');

dropzone.addEventListener('click', () => fileInput.click());
fileInput.addEventListener('change', () => {
  $('fileName').textContent = fileInput.files.length
    ? fileInput.files[0].name
    : 'No file selected';
});
['dragenter', 'dragover'].forEach((ev) =>
  dropzone.addEventListener(ev, (e) => {
    e.preventDefault();
    dropzone.classList.add('is-over');
  })
);
['dragleave', 'drop'].forEach((ev) =>
  dropzone.addEventListener(ev, (e) => {
    e.preventDefault();
    dropzone.classList.remove('is-over');
  })
);
dropzone.addEventListener('drop', (e) => {
  if (e.dataTransfer.files.length) {
    fileInput.files = e.dataTransfer.files;
    $('fileName').textContent = e.dataTransfer.files[0].name;
  }
});

$('loadSample').addEventListener('click', () => {
  $('pgnText').value = SAMPLE_PGN;
});

$('analyseBtn').addEventListener('click', analyse);

async function analyse() {
  const err = $('uploadErr');
  err.textContent = '';

  const active = document.querySelector('.tab.is-active').dataset.tab;
  const form = new FormData();
  if (active === 'file') {
    if (!fileInput.files.length) {
      err.textContent = 'Choose a PGN file first.';
      return;
    }
    form.append('file', fileInput.files[0]);
  } else if (active === 'paste') {
    if (!$('pgnText').value.trim()) {
      err.textContent = 'Paste a PGN first.';
      return;
    }
    form.append('pgn_text', $('pgnText').value);
  } else {
    if (!$('lichessUrl').value.trim()) {
      err.textContent = 'Paste a Lichess link first.';
      return;
    }
    form.append('lichess_url', $('lichessUrl').value.trim());
  }

  form.append('nodes', $('nodes').value);
  form.append('generator', $('generator').value);
  form.append('persona', $('persona').value);
  form.append('audience', $('audience').value);
  form.append('openings', $('openings').checked ? 'true' : 'false');

  $('analyseBtn').disabled = true;
  try {
    const res = await fetch('/api/chess/games', { method: 'POST', body: form });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'upload failed');
    state.jobId = data.job_id;
    $('logBox').textContent = '';
    $('barFill').style.width = '0%';
    showStep(2);
    startPolling();
  } catch (e) {
    err.textContent = e.message;
  } finally {
    $('analyseBtn').disabled = false;
  }
}

/* ------------------------------------------------------------- the poller */

function startPolling() {
  stopPolling();
  state.poll = setInterval(tick, 600);
  tick();
}
function stopPolling() {
  if (state.poll) clearInterval(state.poll);
  state.poll = null;
}

async function tick() {
  if (!state.jobId) return;
  let snap;
  try {
    const res = await fetch(`/api/chess/games/${state.jobId}`);
    if (!res.ok) throw new Error('lost the job');
    snap = await res.json();
  } catch (e) {
    return; // a dropped poll is not worth tearing the page down over
  }
  const previous = state.snap;
  state.snap = snap;

  $('barFill').style.width = `${snap.pct}%`;
  $('stageText').textContent = snap.stage;
  const log = $('logBox');
  if (log.textContent !== snap.log.join('\n')) {
    log.textContent = snap.log.join('\n');
    log.scrollTop = log.scrollHeight;
  }

  if (snap.status === 'error') {
    stopPolling();
    $('stageText').innerHTML = `<span class="err">${escapeHtml(snap.error || 'failed')}</span>`;
    return;
  }

  const wasReady = previous && (previous.status === 'ready' || previous.status === 'narrated');
  if ((snap.status === 'ready' || snap.status === 'narrated') && !wasReady) {
    renderReview(snap);
  }

  if (snap.status === 'ready' && !snap.audio) {
    stopPolling();
    $('narrateBtn').disabled = false;
    $('narrateBtn').textContent = 'Turn into audio →';
    if (snap.error) $('reviewErr').textContent = snap.error;
    showStep(3);
  } else if (snap.status === 'narrated') {
    stopPolling();
    $('narrateBtn').disabled = false;
    $('narrateBtn').textContent = 'Re-generate audio';
    renderAudio(snap);
    showStep(4);
  }
}

/* --------------------------------------------------------- review screen */

function renderReview(snap) {
  const m = snap.meta || {};
  $('gameTitle').textContent = `${m.white} vs ${m.black}`;

  const bits = [m.result];
  if (m.eco || m.opening) bits.push(`${m.eco || ''} ${m.opening || ''}`.trim());
  if (m.event) bits.push(m.event);
  if (m.date) bits.push(m.date);
  bits.push(`${m.total_plies} half-moves`);
  $('gameSub').textContent = bits.filter(Boolean).join('  ·  ');

  state.movesByPly = {};
  (snap.moves || []).forEach((mv) => { state.movesByPly[mv.ply] = mv; });

  renderStatCards(snap);
  renderVerdict(snap.verification, snap.script);
  renderBeats(snap);
}

function card(k, v, note, tone) {
  const c = el('div', 'card');
  c.append(el('div', 'k', k));
  c.append(el('div', `v${tone ? ' ' + tone : ''}`, v));
  if (note) c.append(el('div', 'n', note));
  return c;
}

function renderStatCards(snap) {
  const box = $('statCards');
  box.innerHTML = '';

  (snap.players || []).forEach((p) => {
    const tone = p.accuracy >= 95 ? 'good' : p.accuracy >= 85 ? '' : 'warn';
    const note = p.errors
      ? `${p.errors} error${p.errors > 1 ? 's' : ''}: ${p.error_moves.join(', ')}`
      : 'no errors';
    box.append(card(p.name, `${p.accuracy}%`, note, tone));
  });

  if (snap.arc) {
    box.append(card('Narrative arc', snap.arc.archetype,
      snap.arc.turning_point_san ? `turning point ${snap.arc.turning_point_san}` : ''));
  }
  if (typeof snap.motif_count === 'number') {
    box.append(card('Tactical motifs', String(snap.motif_count), 'detected from board geometry'));
  }
  const b = snap.baselines || {};
  if (b.selected_seconds) {
    const ratio = b.every_move_seconds
      ? ` · ${(b.every_move_seconds / b.selected_seconds).toFixed(1)}x shorter`
      : '';
    box.append(card('Pacing', `${b.selected_seconds}s`,
      `every-move baseline ${b.every_move_seconds}s${ratio}`));
  }
  if (snap.opening_report && snap.opening_report.name) {
    box.append(card('Opening', snap.opening_report.name, snap.opening_report.describe));
  }
  if (snap.script_meta) {
    const t = snap.timings || {};
    box.append(card('Writer', snap.script_meta.generator,
      `${snap.script_meta.total_words} words${t.script_seconds ? ` in ${t.script_seconds}s` : ''}`));
  }
  if (typeof snap.cost_usd === 'number') {
    box.append(card('Cost', `$${snap.cost_usd.toFixed(3)}`, snap.model || ''));
  }
}

function renderVerdict(v, lines) {
  const box = $('verdictBox');
  if (!v) { box.className = 'verdict'; box.textContent = ''; return; }
  box.className = `verdict ${v.errors ? 'dirty' : 'clean'}`;
  box.innerHTML = '';
  const head = el('strong', null,
    v.errors
      ? `${v.errors} unsupported claim${v.errors > 1 ? 's' : ''} found`
      : 'Verified — every claim is backed by the facts the model was given');
  box.append(head);
  const n = (lines || []).length;
  box.append(document.createTextNode(
    `${n} beat${n === 1 ? '' : 's'} checked · ` +
    `${v.errors_per_hundred_beats} errors per 100 beats · ` +
    `${v.warnings} warning${v.warnings === 1 ? '' : 's'}`
  ));
}

function renderBeats(snap) {
  const box = $('beats');
  box.innerHTML = '';
  (snap.script || []).forEach((beat) => box.append(beatCard(beat)));
}

function beatCard(beat) {
  const wrap = el('div', 'beat');
  wrap.dataset.index = beat.index;

  const side = el('div', 'beat-side');
  side.append(el('div', 'beat-type', beat.beat_type.replace(/_/g, ' ')));

  const lastPly = beat.plies.length ? beat.plies[beat.plies.length - 1] : null;
  const move = lastPly !== null ? state.movesByPly[lastPly] : null;
  if (move) {
    side.append(renderBoard(move.fen_after, move.uci));
    const sans = beat.plies
      .map((p) => state.movesByPly[p])
      .filter(Boolean)
      .map((mv) => `${mv.move_number}${mv.color === 'white' ? '.' : '...'}${mv.san}`);
    side.append(el('div', 'beat-moves', sans.join('  ')));
  } else {
    side.append(el('div', 'beat-moves', 'no moves — framing'));
  }
  wrap.append(side);

  const main = el('div', 'beat-main');
  const ta = el('textarea');
  ta.value = beat.text;
  ta.rows = 3;
  ta.addEventListener('input', () => {
    autoGrow(ta);
    scheduleRecheck();
  });
  main.append(ta);

  const meta = el('div', 'beat-meta');
  meta.append(el('span', 'w', `${beat.words} words`));
  meta.append(el('span', 's', `~${beat.est_seconds}s spoken`));
  main.append(meta);

  const findings = el('div', 'beat-findings');
  main.append(findings);
  wrap.append(main);

  applyFindings(wrap, beat.findings || []);
  requestAnimationFrame(() => autoGrow(ta));
  window.addEventListener('resize', () => autoGrow(ta));
  return wrap;
}

function autoGrow(ta) {
  ta.style.height = 'auto';
  ta.style.height = `${Math.max(ta.scrollHeight, 92)}px`;
}

function applyFindings(wrap, findings) {
  const box = wrap.querySelector('.beat-findings');
  box.innerHTML = '';
  wrap.classList.remove('has-error', 'has-warn');
  findings.forEach((f) => {
    const isError = f.severity === 'error';
    const node = el('div', `finding ${isError ? 'error' : 'warning'}`);
    node.append(el('code', null, f.check));
    node.append(document.createTextNode(f.message));
    box.append(node);
    wrap.classList.add(isError ? 'has-error' : 'has-warn');
  });
}

/* ----------------------------------------------------------------- board */

function renderBoard(fen, uci) {
  const board = el('div', 'board');
  const from = uci ? uci.slice(0, 2) : null;
  const to = uci ? uci.slice(2, 4) : null;
  const rows = fen.split(' ')[0].split('/');

  rows.forEach((row, r) => {
    let file = 0;
    for (const ch of row) {
      if (/\d/.test(ch)) {
        for (let i = 0; i < Number(ch); i += 1) {
          board.append(square(file++, r, null));
        }
      } else {
        board.append(square(file++, r, ch));
      }
    }
  });

  function square(file, rank, piece) {
    const name = 'abcdefgh'[file] + (8 - rank);
    const light = (file + rank) % 2 === 0;
    const sq = el('div', `sq ${light ? 'light' : 'dark'}${name === from || name === to ? ' hl' : ''}`);
    if (piece) {
      const glyph = el('span', `pc ${piece === piece.toUpperCase() ? 'w' : 'b'}`,
        PIECES[piece.toLowerCase()]);
      sq.append(glyph);
    }
    return sq;
  }
  return board;
}

/* ------------------------------------------------------- re-verification */

function currentLines() {
  return Array.from(document.querySelectorAll('#beats .beat textarea')).map((t) => t.value);
}

function scheduleRecheck() {
  clearTimeout(state.recheckTimer);
  state.recheckTimer = setTimeout(recheck, 900);
}

$('recheckBtn').addEventListener('click', () => recheck());

async function recheck() {
  if (!state.jobId) return;
  const btn = $('recheckBtn');
  btn.disabled = true;
  btn.textContent = 'Checking…';
  try {
    const res = await fetch(`/api/chess/games/${state.jobId}/verify`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ lines: currentLines() }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'check failed');

    renderVerdict(data, data.beats);
    data.beats.forEach((b) => {
      const wrap = document.querySelector(`#beats .beat[data-index="${b.index}"]`);
      if (!wrap) return;
      applyFindings(wrap, b.findings);
      const meta = wrap.querySelector('.beat-meta');
      meta.querySelector('.w').textContent = `${b.words} words`;
      meta.querySelector('.s').textContent = `~${b.est_seconds}s spoken`;
    });
    $('reviewErr').textContent = '';
  } catch (e) {
    $('reviewErr').textContent = e.message;
  } finally {
    btn.disabled = false;
    btn.textContent = 'Re-check text';
  }
}

/* ---------------------------------------------------------------- narrate */

$('narrateBtn').addEventListener('click', narrate);
$('backToText').addEventListener('click', () => showStep(3));

async function narrate() {
  if (!state.jobId) return;
  const btn = $('narrateBtn');
  btn.disabled = true;
  btn.textContent = 'Synthesising…';
  $('reviewErr').textContent = '';
  try {
    const res = await fetch(`/api/chess/games/${state.jobId}/narrate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        lines: currentLines(),
        tts: 'offline',
        player: $('wantPlayer').checked,
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'narration failed');
    startPolling();
  } catch (e) {
    $('reviewErr').textContent = e.message;
    btn.disabled = false;
    btn.textContent = 'Turn into audio →';
  }
}

/* ----------------------------------------------------------- audio screen */

function renderAudio(snap) {
  const audio = snap.audio;
  if (!audio) return;

  const player = $('player');
  const url = `${audio.track_url}?v=${Date.now()}`;
  if (player.dataset.url !== url) {
    player.src = url;
    player.dataset.url = url;
  }

  const box = $('audioCards');
  box.innerHTML = '';
  box.append(card('Runs for', `${Math.round(audio.total_seconds)}s`,
    `${audio.cues.length} beats`));
  box.append(card('Predicted', `${Math.round(audio.estimated_seconds)}s`,
    `drift ${audio.drift_ratio}x`,
    Math.abs(audio.drift_ratio - 1) < 0.15 ? 'good' : 'warn'));
  if (snap.timings && snap.timings.audio_seconds) {
    box.append(card('Synthesised in', `${snap.timings.audio_seconds}s`, 'offline system voice'));
  }
  if (snap.verification) {
    box.append(card('Verification',
      snap.verification.errors ? `${snap.verification.errors} errors` : 'clean',
      'checked again before speaking',
      snap.verification.errors ? 'bad' : 'good'));
  }

  $('dlAudio').href = audio.track_url;
  const dlPlayer = $('dlPlayer');
  if (audio.player_url) {
    dlPlayer.href = audio.player_url;
    dlPlayer.classList.remove('hidden');
  } else {
    dlPlayer.classList.add('hidden');
  }

  const cues = $('cues');
  cues.innerHTML = '';
  audio.cues.forEach((cue) => {
    const row = el('div', 'cue');
    row.dataset.start = cue.start_seconds;
    row.dataset.end = cue.start_seconds + cue.duration_seconds;
    row.append(el('span', 't', formatTime(cue.start_seconds)));
    row.append(el('span', 'ty', cue.beat_type.replace(/_/g, ' ')));
    const line = (snap.script || [])[cue.index];
    row.append(el('span', 'tx', line ? line.text : ''));
    row.append(el('span', 'd', `${Math.round(cue.duration_seconds)}s`));
    row.addEventListener('click', () => {
      player.currentTime = cue.start_seconds + 0.05;
      player.play();
    });
    cues.append(row);
  });

  player.ontimeupdate = () => {
    const t = player.currentTime;
    cues.querySelectorAll('.cue').forEach((row) => {
      row.classList.toggle('is-playing',
        t >= Number(row.dataset.start) && t < Number(row.dataset.end));
    });
  };
}

function formatTime(seconds) {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${String(s).padStart(2, '0')}`;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

showStep(1);
