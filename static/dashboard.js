"use strict";

const $ = (id) => document.getElementById(id);
const ui = {
  state: null,
  bars: [],
  trade: null,
  symbol: null,
  equity: [],
  pending: null,
  selectedTab: "chat",
  sending: false,
  latestLogs: [],
  toastTimer: null,
};

const finite = (value) => Number.isFinite(Number(value));
const numeric = (value, fallback = 0) => finite(value) ? Number(value) : fallback;
const formatMoney = (value, digits = 2) => {
  const n = Number(value);
  return Number.isFinite(n) ? new Intl.NumberFormat(undefined, {
    style: "currency", currency: "USD", minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(n) : "—";
};
const formatNumber = (value, digits = 4) => {
  const n = Number(value);
  return Number.isFinite(n) ? n.toLocaleString(undefined, {
    minimumFractionDigits: 0, maximumFractionDigits: digits,
  }) : "—";
};
function priceDigits(symbol, value) {
  return (String(symbol || "").includes("/") || Math.abs(Number(value)) < 1) ? 4 : 2;
}
function formatPrice(value, symbol) {
  const digits = priceDigits(symbol, value);
  return formatMoney(value, digits);
}
function relativeTime(dateValue) {
  const date = new Date(dateValue);
  if (!Number.isFinite(date.getTime())) return "not yet";
  const seconds = Math.max(0, Math.floor((Date.now() - date.getTime()) / 1000));
  if (seconds < 10) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}
function localClock(dateValue = new Date()) {
  const date = new Date(dateValue);
  if (!Number.isFinite(date.getTime())) return "—";
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
function showToast(message, kind = "") {
  const item = document.createElement("div");
  item.className = `toast ${kind}`.trim();
  item.textContent = String(message);
  $("toastRegion").appendChild(item);
  window.setTimeout(() => item.remove(), 4200);
}

function appendChat(role, text, type = "text") {
  $("chatEmpty").hidden = true;
  const allowedRoles = new Set(["user", "bot"]);
  const allowedTypes = new Set(["trade", "proposal", "error"]);
  const message = document.createElement("div");
  message.className = `chat-message ${allowedRoles.has(role) ? role : "bot"}`;
  if (role === "bot" && allowedTypes.has(type)) message.classList.add(type);
  const body = document.createElement("span");
  body.textContent = String(text ?? "");
  const stamp = document.createElement("time");
  stamp.className = "message-time";
  stamp.dateTime = new Date().toISOString();
  stamp.textContent = localClock();
  message.append(body, stamp);
  $("chatBox").insertBefore(message, $("chatTyping"));
  $("chatBox").scrollTop = $("chatBox").scrollHeight;
}

function setConnection(online, stale = false) {
  const pill = $("connectionBadge");
  pill.className = `connection-pill ${online ? (stale ? "stale" : "online") : "offline"}`;
  $("connectionText").textContent = online ? (stale ? "Connected · data stale" : "API connected") : "Service unavailable";
}

function renderState(data) {
  ui.state = data;
  const hasAccount = data.account_ready === true && numeric(data.equity, 0) > 0;
  const positionList = Array.isArray(data.positions) ? data.positions : [];
  const accountDate = data.account_updated_at ? new Date(data.account_updated_at) : null;
  const accountAgeMs = accountDate && Number.isFinite(accountDate.getTime()) ? Date.now() - accountDate.getTime() : Infinity;
  const accountStale = accountAgeMs > 120_000;

  $("mEquity").textContent = hasAccount ? formatMoney(data.equity) : "—";
  $("mBP").textContent = hasAccount ? formatMoney(data.buying_power) : "—";
  $("mPos").textContent = hasAccount ? String(positionList.length) : "—";
  if (hasAccount) {
    $("accountHint").textContent = accountStale ? `Account snapshot ${relativeTime(accountDate)} · refresh with a new analysis` : `Broker snapshot ${relativeTime(accountDate)}`;
    $("accountHint").classList.toggle("stale-text", accountStale);
    $("updatedAt").textContent = relativeTime(accountDate);
    $("updatedAt").dateTime = accountDate.toISOString();
  } else {
    $("accountHint").textContent = "No broker snapshot yet · ask for analysis";
    $("accountHint").classList.remove("stale-text");
    $("updatedAt").textContent = "waiting for data";
    $("updatedAt").removeAttribute("datetime");
  }
  setConnection(true, hasAccount && accountStale);

  const unrealized = positionList.reduce((sum, position) => sum + numeric(position.unrealized_pl), 0);
  const pl = $("mPL");
  pl.textContent = hasAccount ? `${unrealized >= 0 ? "+" : "−"}${formatMoney(Math.abs(unrealized))}` : "—";
  pl.classList.toggle("positive-text", hasAccount && unrealized >= 0);
  pl.classList.toggle("negative-text", hasAccount && unrealized < 0);
  $("positionCount").textContent = hasAccount ? String(positionList.length) : "—";
  renderPositions(positionList, hasAccount);

  const halted = data.halted === true;
  const running = data.running === true;
  const engineDot = $("engineDot");
  engineDot.className = `engine-dot ${halted ? "halted" : (running ? "running" : "stopped")}`;
  $("engineStatus").textContent = halted ? "Risk halt active" : (running ? "Automation running" : "Automation stopped");
  $("engineHint").textContent = running
    ? "Stopping automation does not close open positions"
    : "Chat analysis is available; automation is off";
  $("startBtn").disabled = running;
  $("stopBtn").disabled = !running;

  const mode = String(data.mode || "PAPER").toUpperCase();
  const modeBadge = $("modeBadge");
  modeBadge.textContent = mode;
  modeBadge.className = `mode-badge ${mode === "LIVE" ? "live" : (mode === "PAPER" ? "paper" : "unknown")}`;
  $("haltAlert").hidden = !halted;
  if (halted) $("haltAlertText").textContent = data.halt_reason || "A configured risk threshold was reached.";

  const symbols = Object.keys(data.bars || {});
  if (symbols.length) {
    const selected = symbols.find((symbol) => data.trades && data.trades[symbol]) || symbols[symbols.length - 1];
    ui.symbol = selected;
    ui.bars = Array.isArray(data.bars[selected]) ? data.bars[selected] : [];
    ui.trade = data.trades ? data.trades[selected] : null;
    $("focusSym").textContent = selected;
    $("chartState").textContent = getBarFreshness(selected, data.bars_updated_at || {}, ui.bars);
    $("candle").setAttribute("aria-label", `${selected} daily candlestick chart. ${ui.bars.length} bars loaded.`);
  } else {
    ui.symbol = null;
    ui.bars = [];
    ui.trade = null;
    $("focusSym").textContent = "Awaiting symbol";
    $("chartState").textContent = "No chart data";
    $("candle").setAttribute("aria-label", "No price chart data. Ask Sentinel to analyze a symbol.");
  }
  ui.equity = Array.isArray(data.equity_curve) ? data.equity_curve : [];
  drawCandles();
  drawEquity();
  if (!ui.equity.length) $("equityHint").textContent = "History appears after the engine completes its first cycle.";
  renderPending();
  renderActivity(Array.isArray(data.log) ? data.log : []);
}

function getBarFreshness(symbol, timestamps, bars) {
  const value = timestamps[symbol] || (bars.length ? bars[bars.length - 1].t : null);
  if (!value) return "Bar time unavailable";
  const parsed = new Date(value);
  if (!Number.isFinite(parsed.getTime())) return "Bar time unavailable";
  return `Last bar · ${parsed.toLocaleDateString([], { month: "short", day: "numeric", timeZone: "UTC" })} UTC`;
}

function renderPositions(positions, hasAccount) {
  const body = $("posBody");
  const table = document.querySelector(".positions-table");
  body.replaceChildren();
  table.classList.toggle("is-empty", !hasAccount || !positions.length);
  if (!hasAccount) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 5;
    cell.className = "table-empty";
    cell.textContent = "Waiting for an account snapshot. Ask Sentinel to check your account.";
    row.appendChild(cell);
    body.appendChild(row);
    return;
  }
  if (!positions.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 5;
    cell.className = "table-empty";
    cell.textContent = "No open positions in the latest broker snapshot.";
    row.appendChild(cell);
    body.appendChild(row);
    return;
  }
  positions.forEach((position) => {
    const row = document.createElement("tr");
    const pnl = numeric(position.unrealized_pl);
    const values = [
      String(position.symbol || "—"),
      formatNumber(position.qty, 6),
      formatPrice(position.avg_entry, position.symbol),
      formatPrice(position.current_price, position.symbol),
      `${pnl >= 0 ? "+" : "−"}${formatMoney(Math.abs(pnl))}`,
    ];
    values.forEach((value, index) => {
      const cell = document.createElement("td");
      cell.textContent = value;
      if (index === 4) cell.className = pnl >= 0 ? "positive-text" : "negative-text";
      row.appendChild(cell);
    });
    body.appendChild(row);
  });
}

function canvasContext(canvas) {
  const rect = canvas.getBoundingClientRect();
  const width = Math.max(1, rect.width);
  const height = Math.max(1, rect.height);
  const ratio = Math.min(window.devicePixelRatio || 1, 3);
  const pixelWidth = Math.max(1, Math.round(width * ratio));
  const pixelHeight = Math.max(1, Math.round(height * ratio));
  if (canvas.width !== pixelWidth || canvas.height !== pixelHeight) {
    canvas.width = pixelWidth;
    canvas.height = pixelHeight;
  }
  const context = canvas.getContext("2d");
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.clearRect(0, 0, width, height);
  return { context, width, height };
}
function drawCentered(context, width, height, message) {
  context.fillStyle = "#8397ad";
  context.font = "11px ui-sans-serif, system-ui, sans-serif";
  context.textAlign = "center";
  context.textBaseline = "middle";
  context.fillText(message, width / 2, height / 2);
}
function drawCandles() {
  const canvas = $("candle");
  if (!canvas) return;
  const { context: ctx, width: W, height: H } = canvasContext(canvas);
  const usable = ui.bars.filter((bar) => [bar.o, bar.h, bar.l, bar.c].every(finite));
  if (usable.length < 2) {
    drawCentered(ctx, W, H, "Analyze a symbol to load the price chart");
    $("tradeInfo").className = "trade-summary";
    $("tradeInfo").replaceChildren();
    const dot = document.createElement("span");
    dot.className = "summary-dot";
    dot.setAttribute("aria-hidden", "true");
    const text = document.createElement("span");
    text.textContent = "No chart data yet. Ask Sentinel to analyze a symbol.";
    $("tradeInfo").append(dot, text);
    return;
  }

  const bars = usable.slice(-60);
  const padL = 12;
  const padR = W < 380 ? 62 : 76;
  const padT = 23;
  const padB = 16;
  const plotWidth = Math.max(30, W - padL - padR);
  const plotHeight = Math.max(50, H - padT - padB);
  let minPrice = Math.min(...bars.map((bar) => Number(bar.l)));
  let maxPrice = Math.max(...bars.map((bar) => Number(bar.h)));
  const span = Math.max(maxPrice - minPrice, Math.abs(maxPrice) * 0.003, 0.0001);
  minPrice -= span * 0.06;
  maxPrice += span * 0.06;
  const totalSpan = maxPrice - minPrice;
  const y = (price) => padT + plotHeight * (1 - (Number(price) - minPrice) / totalSpan);
  const xAxisText = W < 380 ? 9 : 10;

  ctx.strokeStyle = "rgba(90, 117, 145, .20)";
  ctx.fillStyle = "#71869c";
  ctx.font = `${xAxisText}px ui-monospace, monospace`;
  ctx.textAlign = "left";
  ctx.textBaseline = "middle";
  for (let index = 0; index < 5; index += 1) {
    const yPos = padT + plotHeight * index / 4;
    const labelValue = maxPrice - totalSpan * index / 4;
    ctx.beginPath();
    ctx.moveTo(padL, yPos);
    ctx.lineTo(padL + plotWidth, yPos);
    ctx.stroke();
    ctx.fillText(formatNumber(labelValue, priceDigits(ui.symbol, labelValue)), W - padR + 8, yPos);
  }

  const colors = { up: "#52dfbd", down: "#ff737b" };
  const step = plotWidth / bars.length;
  const candleWidth = Math.max(2, Math.min(11, step * 0.52));
  bars.forEach((bar, index) => {
    const center = padL + step * (index + 0.5);
    const color = Number(bar.c) >= Number(bar.o) ? colors.up : colors.down;
    const openY = y(bar.o);
    const closeY = y(bar.c);
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.15;
    ctx.beginPath();
    ctx.moveTo(center, y(bar.h));
    ctx.lineTo(center, y(bar.l));
    ctx.stroke();
    ctx.fillStyle = color;
    ctx.fillRect(center - candleWidth / 2, Math.min(openY, closeY), candleWidth, Math.max(1.5, Math.abs(closeY - openY)));
  });

  const last = bars[bars.length - 1];
  const lastY = y(last.c);
  ctx.strokeStyle = "rgba(138, 184, 255, .74)";
  ctx.setLineDash([4, 4]);
  ctx.beginPath();
  ctx.moveTo(padL, lastY);
  ctx.lineTo(padL + plotWidth, lastY);
  ctx.stroke();
  ctx.setLineDash([]);

  if (ui.trade) {
    const levels = [
      { value: Number(ui.trade.sl), color: "#ff737b", label: "SL" },
      { value: Number(ui.trade.tp), color: "#52dfbd", label: "TP" },
    ];
    levels.forEach((level) => {
      if (!Number.isFinite(level.value) || level.value < minPrice || level.value > maxPrice) return;
      const levelY = y(level.value);
      ctx.strokeStyle = level.color;
      ctx.setLineDash([5, 4]);
      ctx.beginPath();
      ctx.moveTo(padL, levelY);
      ctx.lineTo(padL + plotWidth, levelY);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = level.color;
      ctx.font = "9px ui-monospace, monospace";
      ctx.textAlign = "right";
      ctx.fillText(`${level.label} ${formatNumber(level.value, priceDigits(ui.symbol, level.value))}`, W - padR - 5, levelY - 6);
    });
  }

  ctx.fillStyle = "#edf4fb";
  ctx.font = "600 10px ui-sans-serif, system-ui, sans-serif";
  ctx.textAlign = "left";
  ctx.textBaseline = "top";
  ctx.fillText(ui.symbol || "Market", padL + 2, 5);
  ctx.fillStyle = Number(last.c) >= Number(bars[0].o) ? colors.up : colors.down;
  const change = Number(bars[0].o) ? (Number(last.c) / Number(bars[0].o) - 1) * 100 : 0;
  ctx.fillText(`${change >= 0 ? "+" : ""}${change.toFixed(2)}%`, padL + 96, 5);
  ctx.textAlign = "right";
  ctx.fillText(formatNumber(last.c, priceDigits(ui.symbol, last.c)), W - 4, lastY - 14);

  const tradeInfo = $("tradeInfo");
  tradeInfo.replaceChildren();
  tradeInfo.className = "trade-summary";
  const dot = document.createElement("span");
  dot.className = `summary-dot ${ui.trade ? "active" : ""}`;
  dot.setAttribute("aria-hidden", "true");
  const text = document.createElement("span");
  if (ui.trade) {
    const pnl = (Number(last.c) - Number(ui.trade.entry)) * Number(ui.trade.qty);
    text.textContent = `Local monitor · ${ui.trade.side || "BUY"} ${formatNumber(ui.trade.qty, 6)} ${ui.symbol} · last ${formatPrice(last.c, ui.symbol)} · estimated P/L ${pnl >= 0 ? "+" : "−"}${formatMoney(Math.abs(pnl))} · SL ${formatPrice(ui.trade.sl, ui.symbol)} · TP ${formatPrice(ui.trade.tp, ui.symbol)}`;
    tradeInfo.classList.add(pnl >= 0 ? "positive" : "negative");
  } else {
    text.textContent = `${ui.symbol} · ${bars.length} daily bars · last close ${formatPrice(last.c, ui.symbol)}`;
  }
  tradeInfo.append(dot, text);
  canvas.setAttribute("aria-label", `${ui.symbol} daily candlestick chart. ${bars.length} bars. Last close ${formatPrice(last.c, ui.symbol)}. Change ${change.toFixed(2)} percent from first displayed open.`);
}

function drawEquity() {
  const canvas = $("equity");
  if (!canvas) return;
  const { context: ctx, width: W, height: H } = canvasContext(canvas);
  const series = ui.equity.map((item) => ({ time: item[0], value: Number(item[1]) }))
    .filter((item) => Number.isFinite(item.value));
  if (series.length < 2) {
    drawCentered(ctx, W, H, "No equity history yet");
    $("equityHint").textContent = ui.state && ui.state.account_ready
      ? "Account snapshot is available; history appears after the first engine cycle."
      : "History appears after the engine completes its first cycle.";
    return;
  }
  const pad = { left: 8, right: 8, top: 15, bottom: 12 };
  const values = series.map((item) => item.value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = Math.max(max - min, 0.01);
  const points = series.map((item, index) => [
    pad.left + (W - pad.left - pad.right) * index / (series.length - 1),
    H - pad.bottom - (H - pad.top - pad.bottom) * (item.value - min) / span,
  ]);
  const gradient = ctx.createLinearGradient(0, pad.top, 0, H - pad.bottom);
  gradient.addColorStop(0, "rgba(82, 223, 189, .20)");
  gradient.addColorStop(1, "rgba(82, 223, 189, 0)");
  ctx.beginPath();
  points.forEach((point, index) => index ? ctx.lineTo(point[0], point[1]) : ctx.moveTo(point[0], point[1]));
  ctx.lineTo(points[points.length - 1][0], H - pad.bottom);
  ctx.lineTo(points[0][0], H - pad.bottom);
  ctx.closePath();
  ctx.fillStyle = gradient;
  ctx.fill();
  ctx.beginPath();
  points.forEach((point, index) => index ? ctx.lineTo(point[0], point[1]) : ctx.moveTo(point[0], point[1]));
  ctx.strokeStyle = "#52dfbd";
  ctx.lineWidth = 1.8;
  ctx.stroke();
  const end = points[points.length - 1];
  ctx.beginPath();
  ctx.arc(end[0], end[1], 3, 0, Math.PI * 2);
  ctx.fillStyle = "#52dfbd";
  ctx.fill();
  const change = values[values.length - 1] - values[0];
  const percent = values[0] ? (change / values[0]) * 100 : 0;
  const lastTime = series[series.length - 1].time;
  $("equityHint").textContent = `Latest ${formatMoney(values[values.length - 1])} · change ${change >= 0 ? "+" : "−"}${formatMoney(Math.abs(change))} (${percent >= 0 ? "+" : ""}${percent.toFixed(2)}%) · ${lastTime ? `last sampled ${relativeTime(lastTime)}` : "time unavailable"}. This is broker-reported account equity.`;
  canvas.setAttribute("aria-label", `Broker-reported equity history. Starts ${formatMoney(values[0])}, latest ${formatMoney(values[values.length - 1])}, change ${percent.toFixed(2)} percent.`);
}

function renderActivity(logs) {
  ui.latestLogs = logs;
  $("logCount").textContent = String(logs.length);
  if (ui.selectedTab !== "log") return;
  const list = $("logBox");
  list.replaceChildren();
  if (!logs.length) {
    list.textContent = "No activity recorded yet.";
    return;
  }
  const safeLevels = new Set(["info", "trade", "warn", "error"]);
  logs.slice(-200).forEach((entry) => {
    const line = document.createElement("div");
    line.className = "activity-line";
    const timestamp = document.createElement("span");
    timestamp.className = "activity-time";
    timestamp.textContent = String(entry.ts || "");
    const level = document.createElement("span");
    const levelName = String(entry.level || "info").toLowerCase();
    level.className = `activity-level ${safeLevels.has(levelName) ? levelName : ""}`.trim();
    level.textContent = safeLevels.has(levelName) ? levelName : "info";
    const message = document.createElement("span");
    message.textContent = String(entry.msg || "");
    line.append(timestamp, level, message);
    list.appendChild(line);
  });
  list.scrollTop = list.scrollHeight;
}

async function refreshState() {
  if (document.hidden) return;
  try {
    const response = await fetch("/api/state", { cache: "no-store", credentials: "same-origin" });
    if (!response.ok) throw new Error(`State request failed (${response.status})`);
    renderState(await response.json());
  } catch (error) {
    setConnection(false);
    $("updatedAt").textContent = "unavailable";
    console.warn("Sentinel state refresh failed", error);
  }
}

function renderStats(data) {
  const trades = numeric(data.trades, 0);
  $("statTrades").textContent = String(trades);
  if (!trades) {
    $("statWinRate").textContent = "—";
    $("statRealized").textContent = "—";
    $("statPF").textContent = "—";
    $("journalHint").textContent = data.msg || "No closed trades in the journal yet. These are descriptive journal metrics, not prop-firm risk checks.";
    return;
  }
  $("statWinRate").textContent = `${numeric(data.win_rate).toFixed(1)}%`;
  const pnl = numeric(data.total_pnl);
  const pnlElement = $("statRealized");
  pnlElement.textContent = `${pnl >= 0 ? "+" : "−"}${formatMoney(Math.abs(pnl))}`;
  pnlElement.className = pnl >= 0 ? "positive-text" : "negative-text";
  const factor = data.profit_factor === "inf" || data.profit_factor === Infinity
    ? "∞" : numeric(data.profit_factor).toFixed(2);
  $("statPF").textContent = factor;
  $("journalHint").textContent = data.metric_notes || "Closed-trade journal statistics only; not account drawdown or firm-rule compliance.";
}
async function refreshStats() {
  try {
    const response = await fetch("/api/stats", { cache: "no-store", credentials: "same-origin" });
    if (!response.ok) throw new Error(`Stats request failed (${response.status})`);
    renderStats(await response.json());
  } catch (error) {
    $("journalHint").textContent = "Journal statistics are currently unavailable.";
    console.warn("Sentinel stats refresh failed", error);
  }
}

function switchTab(tabName) {
  ui.selectedTab = tabName === "log" ? "log" : "chat";
  const chatActive = ui.selectedTab === "chat";
  $("tabChat").classList.toggle("active", chatActive);
  $("tabChat").setAttribute("aria-selected", String(chatActive));
  $("tabLog").classList.toggle("active", !chatActive);
  $("tabLog").setAttribute("aria-selected", String(!chatActive));
  $("chatBox").hidden = !chatActive;
  $("logBox").hidden = chatActive;
  renderActivity(ui.latestLogs);
  if (chatActive) $("chatInput").focus({ preventScroll: true });
}

function secondsRemaining(proposal) {
  const createdAt = Number(proposal && proposal.created_at);
  if (!Number.isFinite(createdAt) || createdAt <= 0) return 0;
  return Math.max(0, Math.ceil(createdAt + 120 - Date.now() / 1000));
}
function renderPending() {
  const proposal = ui.pending;
  const card = $("proposalCard");
  if (!proposal || typeof proposal !== "object") {
    card.hidden = true;
    return;
  }
  card.hidden = false;
  const isExit = proposal.pending_type === "exit";
  const isExpired = secondsRemaining(proposal) <= 0;
  $("proposalTitle").textContent = isExit ? "Position-close review" : "Trade proposal";
  $("proposalSide").textContent = isExit ? "CLOSE" : String(proposal.action || "ORDER").toUpperCase();
  $("proposalSymbol").textContent = isExit
    ? `${(proposal.positions || []).length} position${(proposal.positions || []).length === 1 ? "" : "s"}`
    : String(proposal.symbol || "—");
  $("proposalScore").textContent = isExit ? "Manual exit" : `LLM score ${Math.round(numeric(proposal.confidence) * 100)}% · uncalibrated`;
  $("entryProposalGrid").hidden = isExit;
  const exitList = $("exitPositionList");
  exitList.hidden = !isExit;
  exitList.replaceChildren();
  if (isExit) {
    (proposal.positions || []).forEach((position) => {
      const row = document.createElement("div");
      row.className = "exit-position-row";
      const symbol = document.createElement("strong");
      symbol.textContent = String(position.symbol || "—");
      const quantity = document.createElement("span");
      quantity.textContent = `${formatNumber(position.qty, 6)} units`;
      const pnl = document.createElement("span");
      const value = numeric(position.unrealized_pl);
      pnl.className = value >= 0 ? "positive-text" : "negative-text";
      pnl.textContent = `${value >= 0 ? "+" : "−"}${formatMoney(Math.abs(value))}`;
      row.append(symbol, quantity, pnl);
      exitList.appendChild(row);
    });
  } else {
    $("proposalEntry").textContent = formatPrice(proposal.entry, proposal.symbol);
    $("proposalQty").textContent = formatNumber(proposal.qty, 6);
    $("proposalNotional").textContent = formatMoney(numeric(proposal.entry) * numeric(proposal.qty));
    $("proposalRisk").textContent = formatMoney(proposal.risk_amount);
    $("proposalReward").textContent = formatMoney(proposal.reward_amount);
    $("proposalStop").textContent = formatPrice(proposal.stop_loss, proposal.symbol);
    $("proposalTarget").textContent = formatPrice(proposal.take_profit, proposal.symbol);
    $("proposalExpiry").textContent = isExpired ? "Proposal expired · analyze again" : `Expires in ${String(Math.floor(secondsRemaining(proposal) / 60)).padStart(2, "0")}:${String(secondsRemaining(proposal) % 60).padStart(2, "0")}`;
  }
  if (isExit) $("proposalExpiry").textContent = isExpired ? "Close request expired · request again" : `Expires in ${String(Math.floor(secondsRemaining(proposal) / 60)).padStart(2, "0")}:${String(secondsRemaining(proposal) % 60).padStart(2, "0")}`;
  $("proposalExpiry").classList.toggle("expired", isExpired);
  $("reviewProposalBtn").textContent = isExit ? "Review close" : "Review order";
  $("reviewProposalBtn").disabled = isExpired || ui.sending;
  $("cancelProposalBtn").disabled = ui.sending;
  $("proposalWarningText").textContent = isExit
    ? "Confirming submits market-close requests for the listed positions. Prices can change before the broker processes them."
    : "Market execution may differ from the estimate. Stop and target levels are locally monitored—not broker-protected orders.";
}

function openOrderReview() {
  if (!ui.pending) return;
  if (secondsRemaining(ui.pending) <= 0) {
    renderPending();
    showToast("This proposal has expired. Ask Sentinel for a fresh analysis.", "error");
    return;
  }
  const isExit = ui.pending.pending_type === "exit";
  const mode = ui.state && ui.state.mode ? String(ui.state.mode).toUpperCase() : "PAPER";
  let summary;
  if (isExit) {
    const positions = ui.pending.positions || [];
    summary = `MODE: ${mode}\nCLOSE POSITIONS:\n${positions.map((position) => `${position.symbol} · ${formatNumber(position.qty, 6)} units · P/L ${numeric(position.unrealized_pl) >= 0 ? "+" : "−"}${formatMoney(Math.abs(numeric(position.unrealized_pl)))}`).join("\n")}`;
    $("orderDialogTitle").textContent = positions.length > 1 ? "Close these positions?" : "Close this position?";
    $("placeOrderBtn").textContent = positions.length > 1 ? "Close listed positions" : "Close position";
  } else {
    summary = `MODE: ${mode}\n${String(ui.pending.action).toUpperCase()} ${formatNumber(ui.pending.qty, 6)} ${ui.pending.symbol}\nEstimated entry: ${formatPrice(ui.pending.entry, ui.pending.symbol)}\nEstimated notional: ${formatMoney(numeric(ui.pending.entry) * numeric(ui.pending.qty))}\nEstimated risk to stop: ${formatMoney(ui.pending.risk_amount)}\nEstimated reward to target: ${formatMoney(ui.pending.reward_amount)}\nLLM score: ${Math.round(numeric(ui.pending.confidence) * 100)}% (uncalibrated)\nStop: ${formatPrice(ui.pending.stop_loss, ui.pending.symbol)} · Target: ${formatPrice(ui.pending.take_profit, ui.pending.symbol)}`;
    $("orderDialogTitle").textContent = "Place this market order?";
    $("placeOrderBtn").textContent = "Place market order";
  }
  $("orderDialogSummary").textContent = summary;
  $("orderDialog").showModal();
}

function isConfirmIntent(message) {
  return /^(yes|y|confirm|do it|execute|ok|okay|place order)$/i.test(message.trim());
}
function setBusy(busy) {
  ui.sending = busy;
  $("sendBtn").disabled = busy;
  $("chatInput").disabled = busy;
  $("chatTyping").hidden = !busy;
  $("reviewProposalBtn").disabled = busy || !ui.pending || secondsRemaining(ui.pending) <= 0;
  $("cancelProposalBtn").disabled = busy || !ui.pending;
}
async function sendMessage(message, options = {}) {
  const text = String(message || "").trim();
  if (!text || ui.sending) return;
  if (!options.skipConfirm && ui.pending && isConfirmIntent(text)) {
    appendChat("user", text);
    $("chatInput").value = "";
    openOrderReview();
    return;
  }
  setBusy(true);
  appendChat("user", options.displayText || text);
  if (options.clearInput !== false) $("chatInput").value = "";
  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `Server returned ${response.status}`);
    const pending = Object.prototype.hasOwnProperty.call(data, "pending") ? data.pending : ui.pending;
    ui.pending = pending && typeof pending === "object" ? pending : null;
    if (ui.pending && data.type === "proposal") {
      appendChat("bot", `Analysis complete for ${ui.pending.symbol}. Review the proposal below. Quote and risk checks run again if you confirm.`, "proposal");
    } else if (ui.pending && data.type === "exit-proposal") {
      appendChat("bot", "Close request prepared. Review the listed positions below; nothing is closed until you confirm.", "proposal");
    } else {
      appendChat("bot", data.reply || "(no response)", data.type || "text");
    }
    renderPending();
    await refreshState();
    if (data.type === "trade") showToast("Broker action response received. Check the positions and activity log.", "success");
  } catch (error) {
    if (options.clearInput !== false) $("chatInput").value = text;
    appendChat("bot", `Request failed: ${error.message}. The message is kept in the composer so you can retry.`, "error");
    showToast("The request did not complete. Verify activity before retrying an order command.", "error");
  } finally {
    setBusy(false);
    renderPending();
    $("chatInput").focus({ preventScroll: true });
  }
}

function submitChat(event) {
  event.preventDefault();
  const input = $("chatInput");
  const message = input.value.trim();
  if (!message || ui.sending) return;
  if (ui.pending && isConfirmIntent(message)) {
    appendChat("user", message);
    input.value = "";
    openOrderReview();
    return;
  }
  sendMessage(message, { clearInput: true });
}
async function submitPendingAction(command, displayText) {
  if (!ui.pending || ui.sending) return;
  if ($("orderDialog").open) $("orderDialog").close();
  await sendMessage(command, { skipConfirm: true, displayText });
}

function openAutoStart() {
  const mode = String(ui.state && ui.state.mode || "UNKNOWN").toUpperCase();
  if (!["PAPER", "LIVE"].includes(mode)) {
    showToast("Broker mode is unverified. Automation remains unavailable until PAPER or LIVE mode is configured.", "error");
    return;
  }
  const live = mode === "LIVE";
  $("liveAckWrap").hidden = !live;
  $("liveAckInput").value = "";
  $("confirmStartBtn").disabled = live;
  $("confirmStartBtn").textContent = live ? "Unlock live automation" : "Start automation";
  $("autoStartCopy").textContent = live
    ? "The broker is in LIVE mode. Starting can place real orders without a separate chat confirmation for each trade."
    : "Starting automation lets Sentinel analyze configured markets and submit orders that pass its current checks.";
  if (!live) $("confirmStartBtn").textContent = "Start paper automation";
  $("autoStartDialog").showModal();
}
async function startEngine() {
  $("autoStartDialog").close();
  $("startBtn").disabled = true;
  try {
    const liveMode = ui.state && String(ui.state.mode || "").toUpperCase() === "LIVE";
    const response = await fetch("/api/start", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirm_live: liveMode ? "START LIVE" : "" }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `Server returned ${response.status}`);
    showToast("Automation started. Open positions remain subject to their separate exit logic.", "success");
  } catch (error) {
    appendChat("bot", `Could not start automation: ${error.message}`, "error");
    showToast("Automation did not start.", "error");
  } finally {
    await refreshState();
  }
}
async function stopEngine() {
  $("stopBtn").disabled = true;
  try {
    const response = await fetch("/api/stop", { method: "POST", credentials: "same-origin" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `Server returned ${response.status}`);
    showToast("Automation stopped. This does not close open positions.");
  } catch (error) {
    appendChat("bot", `Could not stop automation: ${error.message}`, "error");
  } finally {
    await refreshState();
  }
}

function setupTabs() {
  document.querySelectorAll(".console-tab").forEach((button, index, buttons) => {
    button.addEventListener("click", () => switchTab(button.dataset.tab));
    button.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      let next = index;
      if (event.key === "ArrowRight") next = (index + 1) % buttons.length;
      if (event.key === "ArrowLeft") next = (index + buttons.length - 1) % buttons.length;
      if (event.key === "Home") next = 0;
      if (event.key === "End") next = buttons.length - 1;
      buttons[next].focus();
      switchTab(buttons[next].dataset.tab);
    });
  });
}

$("chatForm").addEventListener("submit", submitChat);
$("startBtn").addEventListener("click", openAutoStart);
$("stopBtn").addEventListener("click", stopEngine);
$("confirmStartBtn").addEventListener("click", startEngine);
$("cancelStartBtn").addEventListener("click", () => $("autoStartDialog").close());
$("liveAckInput").addEventListener("input", (event) => {
  $("confirmStartBtn").disabled = event.target.value.trim().toUpperCase() !== "START LIVE";
  $("confirmStartBtn").textContent = $("confirmStartBtn").disabled ? "Type START LIVE to continue" : "Start live automation";
});
$("reviewProposalBtn").addEventListener("click", openOrderReview);
$("cancelProposalBtn").addEventListener("click", () => submitPendingAction("cancel", "Cancel proposal"));
$("placeOrderBtn").addEventListener("click", () => submitPendingAction("confirm", ui.pending && ui.pending.pending_type === "exit" ? "Confirm position close" : "Confirm market order"));
$("backToProposalBtn").addEventListener("click", () => $("orderDialog").close());
document.querySelectorAll(".prompt-chip").forEach((button) => {
  button.addEventListener("click", () => sendMessage(button.dataset.prompt));
});
setupTabs();
window.addEventListener("resize", () => { drawCandles(); drawEquity(); });
document.addEventListener("visibilitychange", () => { if (!document.hidden) refreshState(); });
window.setInterval(refreshState, 5000);
window.setInterval(refreshStats, 60_000);
window.setInterval(renderPending, 1000);
refreshState();
refreshStats();
