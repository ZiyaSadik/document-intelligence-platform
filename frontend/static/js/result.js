/* Result page: renders one processed document's extracted fields, tables,
   financial checks and the raw JSON the API returned. */

(function () {
  const name = window.DOCUMENT_NAME;
  const apiUrl = `${API}/documents/${encodeURIComponent(name)}`;
  const LOW_CONFIDENCE = 0.7;

  document.getElementById("api-link").href = apiUrl;

  /* ---- Summary ------------------------------------------------------- */
  function renderSummary(doc) {
    const meta = doc.processing_metadata || {};
    const validation = doc.validation || {};
    const counts = (validation.checks || []).reduce((acc, c) => {
      acc[c.status] = (acc[c.status] || 0) + 1;
      return acc;
    }, {});

    document.getElementById("doc-subtitle").textContent =
      `${titleise(doc.document_type)} · ${meta.pages_processed || 0} page(s) · ` +
      `read via ${meta.ocr_used ? "page rasterisation + vision model" : "native PDF text layer"}`;

    document.getElementById("status-badges").innerHTML = [
      statusBadge(doc.processing_status),
      validation.overall_status
        ? `<span class="badge ${validation.overall_status === "PASS" ? "badge-pass" : "badge-fail"}">Validation ${esc(validation.overall_status)}</span>`
        : "",
      doc.overall_confidence != null ? fmtConfidence(doc.overall_confidence) : "",
    ].join("");

    const tol = validation.tolerance || {};
    const rows = {
      "File type": doc.file_validation ? doc.file_validation.file_type : "—",
      "Pages": meta.pages_processed,
      "Source": meta.ocr_used ? "Vision model" : "PDF text layer",
      "Model": meta.model || "—",
      "Checks": `${counts.PASS || 0} passed · ${counts.FAIL || 0} failed · ${counts.NOT_APPLICABLE || 0} n/a`,
      "Tolerance": tol.absolute != null
        ? `±${tol.absolute} + ${tol.relative} × magnitude`
        : "—",
      "Processing time": meta.processing_time_ms != null
        ? `${(meta.processing_time_ms / 1000).toFixed(2)} s`
        : "—",
      "Processed at": fmtDateTime(meta.processed_at),
    };
    document.getElementById("summary-grid").innerHTML = Object.entries(rows)
      .map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${v == null ? "—" : v}</dd></div>`)
      .join("");
  }

  /* ---- Issues -------------------------------------------------------- */
  function renderIssues(doc) {
    const issues = (doc.validation && doc.validation.issues) || [];
    const missing = (doc.extracted_data && doc.extracted_data.missing_fields) || [];
    if (!issues.length && !missing.length) return;

    const items = issues.map((i) => `<li>${esc(i)}</li>`);
    if (missing.length) {
      items.push(
        `<li class="warn">Not reported in the document: ${missing.map(titleise).map(esc).join(", ")}.</li>`
      );
    }
    document.getElementById("issue-list").innerHTML = items.join("");
    document.getElementById("issues-panel").hidden = false;
  }

  /* ---- Scalar fields -------------------------------------------------- */
  function renderFields(doc) {
    const data = doc.extracted_data || {};
    const rows = [];

    Object.keys(data).forEach((key) => {
      const entry = data[key];
      if (key === "missing_fields") return;
      if (typeof entry === "boolean" || entry === null) {
        if (typeof entry === "boolean") {
          rows.push({ name: key, value: entry ? "Yes" : "No", raw: null,
                      page: null, confidence: null });
        }
        return;
      }
      if (!entry || typeof entry !== "object" || Array.isArray(entry)) return;
      if (!("value" in entry)) return;
      rows.push({
        name: key,
        value: entry.value,
        raw: entry.raw_value,
        page: entry.page_number,
        confidence: entry.confidence,
        evidence: entry.evidence ? entry.evidence.source_text : null,
      });
    });

    (data.additional_fields || []).concat(data.header_fields || []).forEach((f) => {
      rows.push({
        name: f.label, value: f.value, raw: f.raw_value,
        page: f.page_number, confidence: f.confidence,
        evidence: f.evidence ? f.evidence.source_text : null,
      });
    });

    const body = document.getElementById("fields-body");
    if (!rows.length) {
      body.innerHTML = '<tr><td colspan="5" class="empty">No scalar fields extracted.</td></tr>';
      return;
    }

    body.innerHTML = rows
      .map((row) => {
        const isMissing = row.value === null || row.value === undefined || row.value === "";
        const low = row.confidence != null && row.confidence < LOW_CONFIDENCE;
        const display = isMissing
          ? "not reported"
          : esc(typeof row.value === "number" ? fmtNumber(row.value) : row.value);
        return `<tr>
          <td class="field-name">${esc(titleise(row.name))}</td>
          <td class="${isMissing ? "missing" : ""} ${low ? "low-confidence" : ""}"
              ${row.evidence ? `title="${esc(row.evidence)}"` : ""}>
            ${display}
            ${row.evidence ? `<span class="evidence">${esc(row.evidence)}</span>` : ""}
          </td>
          <td class="as-printed">${row.raw ? esc(row.raw) : "&mdash;"}</td>
          <td class="num">${row.page != null ? row.page : "&mdash;"}</td>
          <td>${fmtConfidence(row.confidence)}</td>
        </tr>`;
      })
      .join("");
  }

  /* ---- Key figures ---------------------------------------------------- */
  function renderKeyFigures(doc) {
    const figures = (doc.extracted_data || {}).key_figures;
    if (!figures || !Object.keys(figures).length) return;

    const periods = Object.keys(figures);
    const names = Object.keys(figures[periods[0]] || {});
    if (!names.length) return;

    const head = `<thead><tr><th scope="col">Figure</th>${periods
      .map((p) => `<th scope="col" class="num">${esc(p)}</th>`)
      .join("")}</tr></thead>`;
    const body = `<tbody>${names
      .map((figureName) => {
        const cells = periods
          .map((p) => {
            const cell = figures[p][figureName] || {};
            const missing = cell.value === null || cell.value === undefined;
            return `<td class="num ${missing ? "missing" : ""}"
                        ${cell.source_label ? `title="${esc(cell.source_label)}"` : ""}>
                      ${missing ? "not reported" : esc(fmtNumber(cell.value))}
                    </td>`;
          })
          .join("");
        return `<tr><td class="field-name">${esc(titleise(figureName))}</td>${cells}</tr>`;
      })
      .join("")}</tbody>`;

    document.getElementById("key-figures-table").innerHTML = head + body;
    document.getElementById("key-figures-panel").hidden = false;
  }

  /* ---- Line items ----------------------------------------------------- */
  function renderLineItems(doc) {
    const items = (doc.extracted_data || {}).line_items || [];
    if (!items.length) return;

    const panel = document.getElementById("lines-panel");
    const table = document.getElementById("lines-table");
    const isStatement = items[0] && "values" in items[0];

    if (isStatement) {
      const periods = ((doc.extracted_data.periods) || []).map((p) => p.label);
      const cols = periods.length
        ? periods
        : Array.from(new Set(items.flatMap((i) => Object.keys(i.values || {}))));

      document.getElementById("lines-title").textContent = "Statement line items";
      document.getElementById("lines-subtitle").textContent =
        `${items.length} rows across ${cols.length} reporting period(s). ` +
        "Total rows are shaded; blank cells were not reported.";

      let lastSection = null;
      const rows = [];
      items.forEach((item) => {
        if (item.section && item.section !== lastSection) {
          lastSection = item.section;
          rows.push(
            `<tr class="row-section"><td colspan="${cols.length + 2}">${esc(item.section)}</td></tr>`
          );
        }
        const cells = cols
          .map((c) => {
            const cell = (item.values || {})[c];
            if (!cell || cell.value === null || cell.value === undefined) {
              return '<td class="num missing">&mdash;</td>';
            }
            const low = cell.confidence != null && cell.confidence < LOW_CONFIDENCE;
            return `<td class="num ${low ? "low-confidence" : ""}"
                        title="as printed: ${esc(cell.raw_value)}">${esc(fmtNumber(cell.value))}</td>`;
          })
          .join("");
        rows.push(
          `<tr class="${item.is_total ? "row-total" : ""}">
             <td>${esc(item.label)}</td>${cells}
             <td class="num">${item.page_number != null ? item.page_number : "&mdash;"}</td>
           </tr>`
        );
      });

      table.innerHTML =
        `<thead><tr><th scope="col">Line item</th>` +
        cols.map((c) => `<th scope="col" class="num">${esc(c)}</th>`).join("") +
        `<th scope="col" class="num">Page</th></tr></thead><tbody>${rows.join("")}</tbody>`;
    } else {
      document.getElementById("lines-title").textContent = "Invoice line items";
      document.getElementById("lines-subtitle").textContent = `${items.length} line(s).`;
      table.innerHTML =
        `<thead><tr>
           <th scope="col">Description</th><th scope="col" class="num">Qty</th>
           <th scope="col" class="num">Unit price</th><th scope="col" class="num">Amount</th>
           <th scope="col" class="num">Page</th>
         </tr></thead><tbody>` +
        items
          .map(
            (item) => `<tr>
              <td>${esc(item.description || "—")}</td>
              <td class="num ${item.quantity == null ? "missing" : ""}">${item.quantity == null ? "&mdash;" : esc(fmtNumber(item.quantity))}</td>
              <td class="num ${item.unit_price == null ? "missing" : ""}">${item.unit_price == null ? "&mdash;" : esc(fmtNumber(item.unit_price))}</td>
              <td class="num ${item.amount == null ? "missing" : ""}">${item.amount == null ? "&mdash;" : esc(fmtNumber(item.amount))}</td>
              <td class="num">${item.page_number != null ? item.page_number : "&mdash;"}</td>
            </tr>`
          )
          .join("") +
        "</tbody>";
    }
    panel.hidden = false;
  }

  /* ---- Validation checks ---------------------------------------------- */
  let allChecks = [];

  function renderChecks() {
    const hideNa = document.getElementById("hide-na").checked;
    const checks = hideNa
      ? allChecks.filter((c) => c.status !== "NOT_APPLICABLE")
      : allChecks;
    const body = document.getElementById("checks-body");

    if (!checks.length) {
      body.innerHTML = '<tr><td colspan="8" class="empty">No checks to show.</td></tr>';
      return;
    }

    body.innerHTML = checks
      .map((check) => {
        const operands = Object.entries(check.operands || {})
          .map(([k, v]) => `${titleise(k)} = ${v == null ? "not reported" : fmtNumber(v)}`)
          .join("; ");
        return `<tr class="${check.status === "FAIL" ? "row-fail" : ""}">
          <td class="field-name" ${check.description ? `title="${esc(check.description)}"` : ""}>
            ${esc(check.name.replace("section_sum_check::", "Section sum: "))}
          </td>
          <td>${esc(check.period || "—")}</td>
          <td><code>${esc(check.formula)}</code></td>
          <td class="operands">${esc(operands)}</td>
          <td class="num">${check.calculated_value == null ? "&mdash;" : esc(fmtNumber(check.calculated_value))}</td>
          <td class="num">${check.reported_value == null ? "&mdash;" : esc(fmtNumber(check.reported_value))}</td>
          <td class="num">${check.variance == null ? "&mdash;" : esc(fmtNumber(check.variance))}</td>
          <td>${statusBadge(check.status)}</td>
        </tr>`;
      })
      .join("");
  }

  /* ---- Boot ----------------------------------------------------------- */
  async function load() {
    try {
      const doc = await apiFetch(apiUrl);
      renderSummary(doc);
      renderIssues(doc);
      renderFields(doc);
      renderKeyFigures(doc);
      renderLineItems(doc);

      allChecks = (doc.validation && doc.validation.checks) || [];
      const failed = allChecks.filter((c) => c.status === "FAIL").length;
      document.getElementById("validation-subtitle").textContent =
        `${allChecks.length} check(s) run; ${failed} failed. ` +
        "A check is NOT_APPLICABLE when the document does not report one of its operands.";
      renderChecks();
      document.getElementById("hide-na").addEventListener("change", renderChecks);

      const pretty = JSON.stringify(doc, null, 2);
      document.getElementById("json-view").textContent = pretty;
      document.getElementById("copy-json").addEventListener("click", async (e) => {
        await navigator.clipboard.writeText(pretty);
        e.target.textContent = "Copied";
        setTimeout(() => (e.target.textContent = "Copy"), 1500);
      });
    } catch (error) {
      document.getElementById("doc-subtitle").textContent = error.message;
      document.getElementById("json-view").textContent = error.message;
    }
  }

  load();
})();
