"use strict";

// ============================================================
// Global state
// ============================================================

let currentMatch = null;

// Clock
let clockSeconds = 0;
let clockRunning = false;
let clockInterval = null;

// Pending offset (captured at the moment an outcome button is tapped)
let pendingOffset = null;

// Last ball event index in match.events (for nudge controls)
let lastBallEventIdx = null;

// Selected shot / delivery chips
let selectedShot     = null;
let selectedDelivery = null;

// Refine table: map of event_index -> modified offset
let refineEdits = {};

// Dismissal type selection in wicket panel
let selectedDismissal = null;

// ============================================================
// Constants
// ============================================================

const SHOTS = [
  { label: "Cover drive",    value: "cover_drive"    },
  { label: "On drive",       value: "on_drive"       },
  { label: "Straight drive", value: "straight_drive" },
  { label: "Pull",           value: "pull"           },
  { label: "Hook",           value: "hook"           },
  { label: "Sweep",          value: "sweep"          },
  { label: "Rev. sweep",     value: "reverse_sweep"  },
  { label: "Cut",            value: "cut"            },
  { label: "Flick",          value: "flick"          },
  { label: "Glance",         value: "glance"         },
  { label: "Slog",           value: "slog"           },
  { label: "Slog sweep",     value: "slog_sweep"     },
  { label: "Ramp/Scoop",     value: "ramp"           },
  { label: "Leave/Defend",   value: "leave"          },
];

const DELIVERIES = [
  { label: "Good length", value: "good_length" },
  { label: "Full",        value: "full"        },
  { label: "Short",       value: "short"       },
  { label: "Yorker",      value: "yorker"      },
  { label: "Bouncer",     value: "bouncer"     },
  { label: "Full toss",   value: "full_toss"   },
  { label: "Slower",      value: "slower"      },
  { label: "Swinging",    value: "swinging"    },
];

const DISMISSAL_TYPES = [
  { label: "Bowled",        value: "bowled"       },
  { label: "Caught",        value: "caught"       },
  { label: "LBW",           value: "lbw"          },
  { label: "Run Out",       value: "run_out"      },
  { label: "Stumped",       value: "stumped"      },
  { label: "Hit Wicket",    value: "hit_wicket"   },
];

// ============================================================
// Utility
// ============================================================

function showScreen(name) {
  document.querySelectorAll(".screen").forEach(s => s.classList.remove("active"));
  document.getElementById("screen-" + name).classList.add("active");
}

function toast(msg, duration = 3200) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove("show"), duration);
}

async function api(method, path, body) {
  const opts = { method, headers: { "Content-Type": "application/json" } };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const r = await fetch(path, opts);
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: r.statusText }));
    throw new Error(err.detail || r.statusText);
  }
  return r.json();
}

function parseXI(raw) {
  return raw.split("\n").map(s => s.trim()).filter(Boolean);
}

function esc(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function formatClock(secs) {
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function closeOverlay(name) {
  document.getElementById(name + "-overlay").style.display = "none";
}

function maybeClose(name, event) {
  if (event.target === event.currentTarget) closeOverlay(name);
}

// ============================================================
// Clock management
// ============================================================

function startClock() {
  if (clockRunning) return;
  clockRunning = true;
  document.getElementById("clock-btn").textContent = "Pause";
  clockInterval = setInterval(() => {
    clockSeconds++;
    document.getElementById("clock-num").textContent = formatClock(clockSeconds);
  }, 1000);
}

function pauseClock() {
  if (!clockRunning) return;
  clockRunning = false;
  document.getElementById("clock-btn").textContent = "Resume";
  clearInterval(clockInterval);
  clockInterval = null;
}

function toggleClock() {
  clockRunning ? pauseClock() : startClock();
}

function capturePendingOffset() {
  pendingOffset = clockSeconds;
}

// ============================================================
// Match list (setup screen)
// ============================================================

function scoreLabel(m) {
  const ms = m.match_state || {};
  return `${ms.score || 0}/${ms.wickets || 0} (${ms.overs_complete || 0}.${ms.balls_in_over || 0})`;
}

async function loadMatches() {
  try {
    const matches = await api("GET", "/matches");
    const el = document.getElementById("match-list");
    if (!matches.length) {
      el.innerHTML = '<div class="empty-state">No matches yet — create one below.</div>';
      return;
    }
    el.innerHTML = matches.map(m => `
      <div class="match-card">
        <div>
          <div class="teams">${esc(m.home_team)} vs ${esc(m.away_team)}</div>
          <div class="meta">${m.overs ? m.overs + " overs" : "Unlimited"} &bull; ${m.created_at.slice(0, 10)}</div>
        </div>
        <div style="display:flex;align-items:center;gap:10px">
          ${m.match_state && m.events && m.events.length
            ? `<span class="score-badge">${esc(scoreLabel(m))}</span>` : ""}
          <button class="btn btn-secondary btn-sm" onclick="resumeMatch('${esc(m.match_id)}')">Resume</button>
        </div>
      </div>`
    ).join("");
  } catch (e) {
    console.error(e);
    document.getElementById("match-list").innerHTML = '<div class="empty-state">Could not load matches.</div>';
  }
}

async function resumeMatch(matchId) {
  try {
    currentMatch = await api("GET", `/matches/${matchId}`);
    const ballEvents = currentMatch.events.filter(e => e.event_type === "ball");
    if (!currentMatch.events.length || !currentMatch.batting_team) {
      enterInningsSetup();
    } else {
      enterScoringScreen();
    }
  } catch (e) {
    toast("Error: " + e.message);
  }
}

// ============================================================
// Setup screen helpers
// ============================================================

function updateTossLabels() {
  const home = document.getElementById("home-team").value.trim() || "Home";
  const away = document.getElementById("away-team").value.trim() || "Away";
  document.getElementById("toss-home-label").textContent = home;
  document.getElementById("toss-away-label").textContent = away;
}

function updateXICount(textareaId, hintId) {
  const count = parseXI(document.getElementById(textareaId).value).length;
  const hint = document.getElementById(hintId);
  hint.textContent = `${count} / 11 players entered`;
  hint.style.color = count === 11 ? "var(--green)" : count > 11 ? "var(--red)" : "var(--muted)";
}

// ============================================================
// Create match
// ============================================================

async function submitNewMatch() {
  const homeTeam = document.getElementById("home-team").value.trim();
  const awayTeam = document.getElementById("away-team").value.trim();
  const homeXI   = parseXI(document.getElementById("home-xi").value);
  const awayXI   = parseXI(document.getElementById("away-xi").value);
  const oversRaw = document.getElementById("overs").value.trim();
  const overs    = oversRaw ? parseInt(oversRaw, 10) : null;

  if (!homeTeam || !awayTeam) { toast("Enter both team names."); return; }
  if (homeXI.length < 2 || awayXI.length < 2) { toast("Enter at least 2 players per team."); return; }

  const tossWinnerVal = document.querySelector('input[name="toss-winner"]:checked').value;
  const tossWinner    = tossWinnerVal === "home" ? homeTeam : awayTeam;
  const tossChoice    = document.querySelector('input[name="toss-choice"]:checked').value;

  try {
    currentMatch = await api("POST", "/matches", {
      home_team: homeTeam, away_team: awayTeam,
      home_xi: homeXI, away_xi: awayXI,
      overs, toss_winner: tossWinner, toss_choice: tossChoice,
    });
    toast("Match created!");
    loadMatches();
    enterInningsSetup();
  } catch (e) {
    toast("Error: " + e.message);
  }
}

// ============================================================
// Innings setup screen
// ============================================================

function enterInningsSetup() {
  if (!currentMatch) return;
  const m = currentMatch;

  // Determine which innings number we're setting up
  const inningsStartCount = m.events.filter(e => e.event_type === "innings_start").length;
  const inningsNum = inningsStartCount + 1;

  let battingTeam, bowlingTeam, battingXI, bowlingXI;

  if (inningsNum === 1) {
    const home = m.home_team, away = m.away_team;
    const battingFirst = m.toss_choice === "bat" ? m.toss_winner
                       : (m.toss_winner === home ? away : home);
    battingTeam = battingFirst;
    bowlingTeam = battingFirst === home ? away : home;
    battingXI   = battingFirst === home ? m.home_xi : m.away_xi;
    bowlingXI   = battingFirst === home ? m.away_xi : m.home_xi;
  } else {
    // Innings 2: opposite of innings 1
    battingTeam = m.bowling_team || (m.batting_team === m.home_team ? m.away_team : m.home_team);
    bowlingTeam = m.batting_team || "";
    battingXI   = battingTeam === m.home_team ? m.home_xi : m.away_xi;
    bowlingXI   = bowlingTeam === m.home_team ? m.home_xi : m.away_xi;
  }

  document.getElementById("innings-title").textContent = `Innings ${inningsNum}`;
  document.getElementById("innings-detail").textContent = `${battingTeam} batting vs ${bowlingTeam}`;

  // Target for 2nd innings
  if (inningsNum > 1) {
    const tgt = (m.innings1_score != null ? m.innings1_score : (m.match_state?.score || 0)) + 1;
    document.getElementById("target-display").style.display = "block";
    document.getElementById("target-text").textContent = `${battingTeam} need ${tgt} runs to win`;
  } else {
    document.getElementById("target-display").style.display = "none";
  }

  populateSelect("sel-striker",     battingXI, 0);
  populateSelect("sel-non-striker", battingXI, 1);
  populateSelect("sel-bowler",      bowlingXI, 0);

  window._inningsCtx = { battingTeam, bowlingTeam, battingXI, bowlingXI, inningsNum };
  showScreen("innings");
}

function populateSelect(id, options, defaultIdx = 0) {
  const el = document.getElementById(id);
  el.innerHTML = options.map((p, i) =>
    `<option value="${esc(p)}" ${i === defaultIdx ? "selected" : ""}>${esc(p)}</option>`
  ).join("");
}

async function submitStartInnings() {
  if (!currentMatch || !window._inningsCtx) return;
  const { battingTeam, bowlingTeam, inningsNum } = window._inningsCtx;
  const striker    = document.getElementById("sel-striker").value;
  const nonStriker = document.getElementById("sel-non-striker").value;
  const bowler     = document.getElementById("sel-bowler").value;

  if (striker === nonStriker) { toast("Striker and non-striker must be different players."); return; }

  // Target for 2nd innings
  let target = null;
  if (inningsNum > 1) {
    const m = currentMatch;
    target = (m.innings1_score != null ? m.innings1_score : (m.match_state?.score || 0)) + 1;
  }

  try {
    currentMatch = await api("POST", `/matches/${currentMatch.match_id}/innings`, {
      striker, non_striker: nonStriker, bowler,
      batting_team: battingTeam, bowling_team: bowlingTeam,
      target,
    });
    toast("Innings started!");
    enterScoringScreen();
  } catch (e) {
    toast("Error: " + e.message);
  }
}

// ============================================================
// Scoring screen — entry
// ============================================================

function enterScoringScreen() {
  // Reset local scoring state
  clockSeconds  = 0;
  pendingOffset = null;
  lastBallEventIdx = null;
  selectedShot  = null;
  selectedDelivery = null;

  document.getElementById("clock-num").textContent = "0:00";
  document.getElementById("clock-btn").textContent = "Pause";

  buildChipGrid("shot-grid",     SHOTS,     "shot");
  buildChipGrid("delivery-grid", DELIVERIES, "delivery");

  renderScoringScreen(currentMatch);
  showScreen("score");

  startClock();
}

// ============================================================
// Scoring screen — rendering (single source of truth)
// ============================================================

function renderScoringScreen(m) {
  currentMatch = m;
  const ms = m.match_state || {};

  // Score bar
  document.getElementById("score-num").textContent = `${ms.score || 0}/${ms.wickets || 0}`;
  document.getElementById("score-overs").textContent =
    `(${ms.overs_complete || 0}.${ms.balls_in_over || 0} ov)`;
  document.getElementById("innings-pill").textContent = `INN ${m.innings || 1}`;
  document.getElementById("batting-team-disp").textContent = m.batting_team || "";

  // Chase strip
  const chaseEl = document.getElementById("chase-strip");
  if (ms.target && ms.balls_remaining != null) {
    const need = ms.target - (ms.score || 0);
    chaseEl.style.display = "";
    document.getElementById("chase-need").textContent = `Need ${need} off ${ms.balls_remaining} balls`;
    document.getElementById("chase-rrr").textContent =
      ms.required_run_rate ? `RRR ${ms.required_run_rate}` : "";
  } else {
    chaseEl.style.display = "none";
  }

  // Players
  document.getElementById("striker-name").textContent  = m.striker  || "--";
  document.getElementById("non-striker-name").textContent = m.non_striker || "--";
  document.getElementById("striker-stats").textContent  = batsmanStats(m, m.striker);
  document.getElementById("non-striker-stats").textContent = batsmanStats(m, m.non_striker);
  document.getElementById("bowler-disp").textContent    = m.bowler   || "--";
  document.getElementById("bowler-stats-disp").textContent = bowlerStats(m, m.bowler, m.innings);

  // This over balls
  const overBalls = ballsInOver(m, m.innings, m.current_over);
  const thisOverEl = document.getElementById("this-over-balls");
  thisOverEl.innerHTML = overBalls.map(b => `<span class="${'ob ' + ballClass(b)}">${ballSymbol(b)}</span>`).join("");

  // Partnership
  const partEl = document.getElementById("partnership-disp");
  if (ms.partnership_runs != null) {
    partEl.textContent = `Pship: ${ms.partnership_runs} (${ms.partnership_balls || 0})`;
  } else {
    partEl.textContent = "";
  }

  // Recent balls (last 5 ball events from current innings)
  const allBalls = m.events.filter(e => e.event_type === "ball" && e.innings === m.innings);
  const recent5  = allBalls.slice(-5);
  document.getElementById("recent-balls").innerHTML =
    recent5.map(b => `<span class="${'rb ' + ballClass(b)}">${ballSymbol(b)}</span>`).join(" ");

  // Undo button
  const undoBtn = document.getElementById("undo-btn");
  undoBtn.disabled = allBalls.length === 0;

  // Offset display
  updateOffsetDisplay(m);

  // Commentary
  renderCommentary(m);

  // Auto-show over-complete modal
  if (m.over_complete && !document.getElementById("over-overlay").style.display.includes("flex")) {
    openOverCompleteModal(m);
  }

  // Auto-show innings complete overlay
  if (m.innings_complete && !document.getElementById("innings-overlay").style.display.includes("flex")) {
    openInningsCompleteModal(m);
  }
}

// ============================================================
// Helpers for rendering
// ============================================================

function batsmanStats(m, name) {
  if (!name) return "0 (0)";
  const ms = m.match_state || {};
  if (name === m.striker) return `${ms.striker_runs || 0} (${ms.striker_balls || 0})`;
  if (name === m.non_striker) return `${ms.non_striker_runs || 0} (${ms.non_striker_balls || 0})`;
  return "0 (0)";
}

function bowlerStats(m, bowler, innings) {
  if (!bowler) return "0-0-0-0";
  const balls = m.events.filter(e =>
    e.event_type === "ball" && e.innings === innings && e.bowler === bowler
  );
  let legal = 0, runs = 0, wkts = 0;
  for (const b of balls) {
    const ext = b.outcome?.extras;
    const isLegal = !ext || !["wide", "no_ball"].includes(ext.type);
    if (isLegal) legal++;
    runs += b.outcome?.runs || 0;
    if (ext) runs += ext.runs || 0;
    if (b.outcome?.wicket?.bowler_credited !== false && b.outcome?.wicket) wkts++;
  }
  const overs = Math.floor(legal / 6);
  const rem   = legal % 6;
  return `${overs}.${rem}-0-${runs}-${wkts}`;
}

function ballsInOver(m, innings, over) {
  return m.events.filter(e =>
    e.event_type === "ball" && e.innings === innings && e.over === over
  );
}

function ballClass(b) {
  const o = b.outcome || {};
  if (o.wicket)           return "wicket";
  if (o.is_six)           return "six";
  if (o.is_boundary)      return "four";
  const et = o.extras?.type;
  if (et === "wide")      return "wide";
  if (et === "no_ball")   return "noball";
  if (!o.runs && !o.extras) return "dot";
  return `run${o.runs || ""}`;
}

function ballSymbol(b) {
  const o = b.outcome || {};
  if (o.wicket)           return "W";
  if (o.is_six)           return "6";
  if (o.is_boundary)      return "4";
  const et = o.extras?.type;
  if (et === "wide")      return "wd";
  if (et === "no_ball")   return "nb";
  const r = o.runs || 0;
  return r === 0 ? "•" : String(r);
}

function getBattingXI(m) {
  return m.batting_team === m.home_team ? m.home_xi : m.away_xi;
}
function getBowlingXI(m) {
  return m.batting_team === m.home_team ? m.away_xi : m.home_xi;
}
function getRemainingBatters(m) {
  const xi = getBattingXI(m);
  const dismissed = new Set(
    m.events
      .filter(e => e.event_type === "ball" && e.innings === m.innings && e.outcome?.wicket)
      .map(e => e.outcome.wicket.dismissed_batsman)
  );
  const active = new Set([m.striker, m.non_striker].filter(Boolean));
  return xi.filter(p => !dismissed.has(p) && !active.has(p));
}

// ============================================================
// Chip grids
// ============================================================

function buildChipGrid(containerId, items, type) {
  const el = document.getElementById(containerId);
  el.innerHTML = items.map(item => `
    <button class="chip" data-val="${esc(item.value)}" data-type="${type}"
      onclick="selectChip(this,'${type}','${esc(item.value)}')">${esc(item.label)}</button>
  `).join("");
}

function selectChip(el, type, value) {
  if (type === "shot") {
    selectedShot = selectedShot === value ? null : value;
  } else {
    selectedDelivery = selectedDelivery === value ? null : value;
  }
  // Update visual state for this grid
  const gridId = type === "shot" ? "shot-grid" : "delivery-grid";
  document.getElementById(gridId).querySelectorAll(".chip").forEach(c => {
    c.classList.toggle("sel", c.dataset.val === (type === "shot" ? selectedShot : selectedDelivery));
  });
}

function clearSel(type) {
  if (type === "shot") selectedShot = null;
  else selectedDelivery = null;
  const gridId = type === "shot" ? "shot-grid" : "delivery-grid";
  document.getElementById(gridId).querySelectorAll(".chip").forEach(c => c.classList.remove("sel"));
}

// ============================================================
// Ball recording — quick paths
// ============================================================

async function quickRecord(runs) {
  capturePendingOffset();
  const isBoundary = runs === 4;
  const isSix      = runs === 6;
  await submitBall({
    runs,
    is_boundary: isBoundary,
    is_six: isSix,
    shot_tag: selectedShot,
    delivery_type: selectedDelivery,
    note: document.getElementById("note-input").value.trim() || null,
    video_offset: pendingOffset,
  });
}

async function quickExtra(type) {
  capturePendingOffset();
  await submitBall({
    runs: 0,
    extras_type: type,
    extras_runs: 1,
    delivery_type: selectedDelivery,
    note: document.getElementById("note-input").value.trim() || null,
    video_offset: pendingOffset,
  });
}

async function submitBall(payload) {
  if (!currentMatch) return;
  try {
    const m = await api("POST", `/matches/${currentMatch.match_id}/ball`, payload);
    // Find last ball event index
    lastBallEventIdx = null;
    for (let i = m.events.length - 1; i >= 0; i--) {
      if (m.events[i].event_type === "ball") { lastBallEventIdx = i; break; }
    }
    // Clear inputs
    document.getElementById("note-input").value = "";
    renderScoringScreen(m);
  } catch (e) {
    toast("Error: " + e.message);
  }
}

// ============================================================
// Offset nudge controls
// ============================================================

function updateOffsetDisplay(m) {
  const valEl  = document.getElementById("offset-val");
  const minBtn = document.getElementById("nudge-minus");
  const plusBtn = document.getElementById("nudge-plus");
  if (lastBallEventIdx !== null && m.events[lastBallEventIdx]) {
    const ev = m.events[lastBallEventIdx];
    const off = ev.video_offset;
    valEl.textContent = off != null ? `${off.toFixed(1)} s` : "--";
    minBtn.disabled = off == null;
    plusBtn.disabled = off == null;
  } else {
    valEl.textContent = "--";
    minBtn.disabled = true;
    plusBtn.disabled = true;
  }
}

async function nudgeOffset(delta) {
  if (lastBallEventIdx === null || !currentMatch) return;
  const ev  = currentMatch.events[lastBallEventIdx];
  const cur = ev?.video_offset;
  if (cur == null) return;
  const newOff = Math.max(0, Math.round((cur + delta) * 10) / 10);
  try {
    const m = await api("PATCH",
      `/matches/${currentMatch.match_id}/ball/${lastBallEventIdx}/offset`,
      { offset: newOff }
    );
    renderScoringScreen(m);
  } catch (e) {
    toast("Error: " + e.message);
  }
}

// ============================================================
// Swap strike
// ============================================================

async function swapStrike() {
  if (!currentMatch) return;
  try {
    const m = await api("POST", `/matches/${currentMatch.match_id}/swap-strike`);
    renderScoringScreen(m);
    toast("Strike swapped.");
  } catch (e) {
    toast("Error: " + e.message);
  }
}

// ============================================================
// Undo
// ============================================================

async function undoBall() {
  if (!currentMatch) return;
  try {
    const m = await api("DELETE", `/matches/${currentMatch.match_id}/ball`);
    lastBallEventIdx = null;
    renderScoringScreen(m);
    toast("Last ball undone.");
  } catch (e) {
    toast("Error: " + e.message);
  }
}

// ============================================================
// Wicket panel
// ============================================================

function openWicketPanel() {
  capturePendingOffset();
  if (!currentMatch) return;
  selectedDismissal = null;

  // Dismissed batsman options
  const wDismissed = document.getElementById("w-dismissed");
  const opts = [currentMatch.striker, currentMatch.non_striker].filter(Boolean);
  wDismissed.innerHTML = opts.map(p => `<option value="${esc(p)}">${esc(p)}</option>`).join("");

  // Dismissal type chips
  const chips = document.getElementById("w-type-chips");
  chips.innerHTML = DISMISSAL_TYPES.map(d => `
    <button class="dchip" data-val="${d.value}" onclick="selectDismissal(this,'${d.value}')">${esc(d.label)}</button>
  `).join("");

  // New batsman
  const remaining = getRemainingBatters(currentMatch);
  const wNewBat = document.getElementById("w-new-bat");
  wNewBat.innerHTML = remaining.length
    ? remaining.map(p => `<option value="${esc(p)}">${esc(p)}</option>`).join("")
    : "<option value=''>-- no batters remaining --</option>";

  // Run-out section hidden by default
  document.getElementById("w-runout-section").style.display = "none";
  document.getElementById("w-dismissed").addEventListener("change", updateRunoutSection);

  document.getElementById("w-runs").value = "0";
  document.getElementById("w-fielder").value = "";

  document.getElementById("wicket-overlay").style.display = "flex";
}

function selectDismissal(el, value) {
  selectedDismissal = value;
  document.getElementById("w-type-chips").querySelectorAll(".dchip").forEach(c => {
    c.classList.toggle("sel", c.dataset.val === value);
  });
  updateRunoutSection();
}

function updateRunoutSection() {
  const isRunOut = selectedDismissal === "run_out";
  const ro = document.getElementById("w-runout-section");
  ro.style.display = isRunOut ? "" : "none";
  if (isRunOut && currentMatch) {
    const striker    = currentMatch.striker || "";
    const nonStriker = currentMatch.non_striker || "";
    const dismissed  = document.getElementById("w-dismissed").value;
    const survivor   = dismissed === striker ? nonStriker : striker;
    document.getElementById("w-strike-after").innerHTML = `
      <label><input type="radio" name="w-strike" value="${esc(survivor)}" checked> ${esc(survivor)} on strike</label>
      <label><input type="radio" name="w-strike" value="${esc(dismissed)}"> New batter on strike</label>
    `;
  }
}

async function submitWicket() {
  if (!selectedDismissal) { toast("Select a dismissal type."); return; }

  const dismissed  = document.getElementById("w-dismissed").value;
  const fielder    = document.getElementById("w-fielder").value.trim() || null;
  const newBatsman = document.getElementById("w-new-bat").value || null;
  const runs       = parseInt(document.getElementById("w-runs").value, 10) || 0;

  const payload = {
    runs,
    dismissal_type: selectedDismissal,
    dismissed_batsman: dismissed,
    fielder,
    new_batsman: newBatsman,
    is_boundary: false,
    is_six: false,
    shot_tag: selectedShot,
    delivery_type: selectedDelivery,
    note: document.getElementById("note-input").value.trim() || null,
    video_offset: pendingOffset,
  };

  // Run-out: check desired striker after wicket
  let swapAfter = false;
  if (selectedDismissal === "run_out") {
    const checked = document.querySelector('input[name="w-strike"]:checked');
    if (checked) {
      // If the chosen "on strike" batter is the NEW batter (unusual), we need a swap
      // After the backend places new_batsman in the dismissed batter's slot,
      // check if that results in the wrong player on strike.
      const m = currentMatch;
      const strikerAfterBackend = dismissed === m.striker ? newBatsman : m.striker;
      if (checked.value !== strikerAfterBackend) swapAfter = true;
    }
  }

  closeOverlay("wicket");

  try {
    let m = await api("POST", `/matches/${currentMatch.match_id}/ball`, payload);
    lastBallEventIdx = null;
    for (let i = m.events.length - 1; i >= 0; i--) {
      if (m.events[i].event_type === "ball") { lastBallEventIdx = i; break; }
    }
    if (swapAfter) {
      m = await api("POST", `/matches/${m.match_id}/swap-strike`);
    }
    document.getElementById("note-input").value = "";
    renderScoringScreen(m);
  } catch (e) {
    toast("Error: " + e.message);
  }
}

// ============================================================
// Manual entry panel
// ============================================================

function openManualPanel() {
  capturePendingOffset();
  document.getElementById("m-runs").value = "0";
  document.getElementById("m-extras-type").value = "";
  document.getElementById("m-extras-runs").value = "1";
  document.getElementById("m-boundary").checked = false;
  document.getElementById("m-six").checked = false;
  document.getElementById("m-extras-runs-row").style.display = "none";
  document.getElementById("manual-overlay").style.display = "flex";
}

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("m-extras-type").addEventListener("change", function () {
    document.getElementById("m-extras-runs-row").style.display = this.value ? "" : "none";
  });
});

async function submitManual() {
  const runs       = parseInt(document.getElementById("m-runs").value, 10) || 0;
  const extrasType = document.getElementById("m-extras-type").value || null;
  const extrasRuns = parseInt(document.getElementById("m-extras-runs").value, 10) || 1;
  const isBoundary = document.getElementById("m-boundary").checked;
  const isSix      = document.getElementById("m-six").checked;

  const payload = {
    runs,
    extras_type: extrasType || undefined,
    extras_runs: extrasType ? extrasRuns : undefined,
    is_boundary: isBoundary,
    is_six: isSix,
    shot_tag: selectedShot,
    delivery_type: selectedDelivery,
    note: document.getElementById("note-input").value.trim() || null,
    video_offset: pendingOffset,
  };

  closeOverlay("manual");
  await submitBall(payload);
}

// ============================================================
// Over complete modal
// ============================================================

function openOverCompleteModal(m) {
  const over = m.current_over + 1; // 0-indexed → 1-indexed display
  const balls = ballsInOver(m, m.innings, m.current_over);
  const overRuns = balls.reduce((sum, b) => {
    const o = b.outcome || {};
    return sum + (o.runs || 0) + (o.extras?.runs || 0);
  }, 0);

  document.getElementById("over-title").textContent = `Over ${over} Complete`;
  document.getElementById("over-summary").textContent =
    `${overRuns} run${overRuns !== 1 ? "s" : ""} off the over.`;

  // Populate bowler options (all bowling XI except previous bowler)
  const bowlingXI = getBowlingXI(m).filter(p => p !== m.bowler);
  const sel = document.getElementById("new-bowler-sel");
  sel.innerHTML = bowlingXI.length
    ? bowlingXI.map(p => `<option value="${esc(p)}">${esc(p)}</option>`).join("")
    : `<option value="${esc(m.bowler || "")}">${esc(m.bowler || "")} (re-bowl)</option>`;

  document.getElementById("over-overlay").style.display = "flex";
}

async function submitNewOver() {
  const bowler = document.getElementById("new-bowler-sel").value;
  if (!bowler) { toast("Select a bowler."); return; }
  closeOverlay("over");
  try {
    const m = await api("POST", `/matches/${currentMatch.match_id}/over`, { bowler });
    renderScoringScreen(m);
  } catch (e) {
    toast("Error: " + e.message);
  }
}

// ============================================================
// End innings
// ============================================================

async function confirmEndInnings() {
  if (!currentMatch) return;
  const ms = currentMatch.match_state || {};
  const confirmed = window.confirm(
    `End innings now?\n\n${currentMatch.batting_team} — ${ms.score || 0}/${ms.wickets || 0}\n\nThis will lock the innings and set the chase target.`
  );
  if (!confirmed) return;
  pauseClock();
  try {
    const m = await api("POST", `/matches/${currentMatch.match_id}/end-innings`);
    renderScoringScreen(m);
  } catch (e) {
    toast("Error: " + e.message);
  }
}

function openInningsCompleteModal(m) {
  const ms  = m.match_state || {};
  const inn = m.innings || 1;

  document.getElementById("innings-done-heading").textContent =
    inn === 1 ? "Innings 1 Complete" : "Match Complete";
  document.getElementById("innings-done-team").textContent = m.batting_team || "";
  document.getElementById("innings-done-score").textContent = `${ms.score || 0}/${ms.wickets || 0}`;
  document.getElementById("innings-done-overs").textContent =
    `(${ms.overs_complete || 0}.${ms.balls_in_over || 0} overs)`;

  const nextBtn  = document.getElementById("innings-next-btn");
  const doneBtn  = document.getElementById("innings-done-btn");
  const targetEl = document.getElementById("innings-done-target");

  if (inn === 1) {
    const target = (ms.score || 0) + 1;
    targetEl.textContent = `Target: ${target} runs`;
    nextBtn.style.display = "";
    doneBtn.style.display = "none";
  } else if (m.match_complete) {
    targetEl.textContent = ms.score >= ms.target
      ? `${m.batting_team} win!` : `${m.bowling_team} win!`;
    nextBtn.style.display = "none";
    doneBtn.style.display = "";
  } else {
    targetEl.textContent = "";
    nextBtn.style.display = "";
    doneBtn.style.display = "none";
  }

  document.getElementById("innings-overlay").style.display = "flex";
}

async function goToNextInnings() {
  closeOverlay("innings");
  pauseClock();
  // Refresh match state then go to innings setup
  try {
    currentMatch = await api("GET", `/matches/${currentMatch.match_id}`);
  } catch (_) {}
  enterInningsSetup();
}

// ============================================================
// Commentary
// ============================================================

async function genCommentary(mock) {
  if (!currentMatch) return;
  toast("Generating commentary...", 10000);
  try {
    const m = await api("POST", `/matches/${currentMatch.match_id}/commentary`, { mock });
    renderScoringScreen(m);
    toast("Commentary ready.");
  } catch (e) {
    toast("Error: " + e.message);
  }
}

function renderCommentary(m) {
  const el = document.getElementById("commentary-lines");
  if (!m.commentary || !m.commentary.length) {
    el.innerHTML = '<div class="empty-state" style="font-size:13px">No commentary yet.</div>';
    return;
  }
  // Show last 5 lines (most recent first)
  const recent = m.commentary.slice(-5).reverse();
  el.innerHTML = recent.map(c => `
    <div class="commentary-entry">
      <div class="commentary-meta">${esc(c.event_type)} #${c.event_index}</div>
      <div class="commentary-text">${esc(c.text)}</div>
    </div>
  `).join("");
}

// ============================================================
// Refine timings panel
// ============================================================

function openRefinePanel() {
  if (!currentMatch) return;
  refineEdits = {};

  const ballEvents = currentMatch.events
    .map((e, i) => ({ ...e, _idx: i }))
    .filter(e => e.event_type === "ball" && e.innings === currentMatch.innings);

  const tbody = document.getElementById("refine-body");
  tbody.innerHTML = ballEvents.map(b => {
    const o   = b.outcome || {};
    const sym = ballSymbol(b);
    const dt  = b.delivery_type ? esc(b.delivery_type.replace(/_/g, " ")) : "";
    const off = b.video_offset != null ? b.video_offset : "";
    return `<tr data-idx="${b._idx}">
      <td>${b.over + 1}.${b.ball}</td>
      <td>${esc(sym)}</td>
      <td style="color:var(--muted)">${dt}</td>
      <td><input type="number" step="0.1" value="${off}" onchange="markRefineEdit(${b._idx},this.value)"></td>
      <td>
        <button class="nudge-btn" onclick="nudgeRefine(${b._idx},-1)">-1s</button>
        <button class="nudge-btn" onclick="nudgeRefine(${b._idx},+1)">+1s</button>
      </td>
    </tr>`;
  }).join("");

  document.getElementById("refine-overlay").style.display = "flex";
}

function markRefineEdit(idx, val) {
  refineEdits[idx] = parseFloat(val);
}

function nudgeRefine(idx, delta) {
  const row = document.querySelector(`#refine-body tr[data-idx="${idx}"]`);
  if (!row) return;
  const input = row.querySelector("input[type=number]");
  const cur   = parseFloat(input.value) || 0;
  const next  = Math.max(0, Math.round((cur + delta) * 10) / 10);
  input.value = next;
  refineEdits[idx] = next;
}

async function saveRefineTimings() {
  if (!currentMatch) return;
  const entries = Object.entries(refineEdits);
  if (!entries.length) { closeOverlay("refine"); return; }
  toast("Saving timings...", 8000);
  try {
    let m = currentMatch;
    for (const [idx, offset] of entries) {
      m = await api("PATCH",
        `/matches/${m.match_id}/ball/${idx}/offset`,
        { offset }
      );
    }
    refineEdits = {};
    renderScoringScreen(m);
    closeOverlay("refine");
    toast("Timings saved.");
  } catch (e) {
    toast("Error: " + e.message);
  }
}

// ============================================================
// Boot
// ============================================================

document.addEventListener("DOMContentLoaded", () => {
  loadMatches();

  document.getElementById("home-team").addEventListener("input", updateTossLabels);
  document.getElementById("away-team").addEventListener("input", updateTossLabels);
  document.getElementById("home-xi").addEventListener("input", () =>
    updateXICount("home-xi", "home-xi-count"));
  document.getElementById("away-xi").addEventListener("input", () =>
    updateXICount("away-xi", "away-xi-count"));
});
