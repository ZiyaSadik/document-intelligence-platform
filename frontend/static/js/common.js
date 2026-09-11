/* Shared helpers for the dashboard pages.
   Everything here talks to the same public REST API an evaluator would call
   directly, so the UI can never show something the API does not return. */

const API = "/api/v1";

/** Escape text before it goes anywhere near innerHTML. */
function esc(value) {
  if (value === null || value === undefined) return "";
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/** Fetch JSON, turning the API's error envelope into a thrown Error. */
async function apiFetch(path, options = {}) {
  const response = await fetch(path, options);
  let body = null;
  try {
    body = await response.json();
  } catch (_) {
    /* A non-JSON body (e.g. a proxy error page) is handled below. */
  }
  if (!response.ok) {
    const err = body && body.error ? body.error : {};
    const error = new Error(err.message || `Request failed (${response.status}).`);
    error.code = err.code || `HTTP_${response.status}`;
    error.status = response.status;
    throw error;
  }
  return body;
}

/** Format a number with thousands separators; negatives in parentheses. */
function fmtNumber(value) {
  if (value === null || value === undefined || value === "") return null;
  if (typeof value !== "number") return String(value);
  const abs = Math.abs(value).toLocaleString(undefined, {
    minimumFractionDigits: Number.isInteger(value) ? 0 : 2,
    maximumFractionDigits: 2,
  });
  return value < 0 ? `(${abs})` : abs;
}

function fmtConfidence(value) {
  if (value === null || value === undefined) return "&mdash;";
  const pct = Math.round(value * 100);
  const cls = value < 0.7 ? "badge-warn" : "badge-info";
  return `<span class="badge ${cls}">${pct}%</span>`;
}

function fmtDateTime(value) {
  if (!value) return "&mdash;";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return esc(value);
  return date.toLocaleString(undefined, {
    year: "numeric", month: "short", day: "2-digit",
    hour: "2-digit", minute: "2-digit",
  });
}

function statusBadge(status) {
  const map = { PASS: "badge-pass", FAIL: "badge-fail", FAILED: "badge-fail",
                NOT_APPLICABLE: "badge-na" };
  const label = status === "NOT_APPLICABLE" ? "N/A" : status;
  return `<span class="badge ${map[status] || "badge-na"}">${esc(label)}</span>`;
}

function titleise(key) {
  return String(key)
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}
