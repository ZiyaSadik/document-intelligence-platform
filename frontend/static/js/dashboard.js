/* Dashboard: upload form plus the processed-document list. */

(function () {
  const form = document.getElementById("upload-form");
  const button = document.getElementById("process-btn");
  const statusBox = document.getElementById("upload-status");
  const tbody = document.getElementById("documents-body");
  const summary = document.getElementById("list-summary");
  const search = document.getElementById("search");
  const typeFilter = document.getElementById("type-filter");
  const refresh = document.getElementById("refresh-btn");

  function setStatus(kind, html) {
    statusBox.hidden = false;
    statusBox.innerHTML = `<div class="banner banner-${kind}">${html}</div>`;
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const fileInput = document.getElementById("file");
    const file = fileInput.files[0];
    if (!file) {
      setStatus("error", "<strong>Choose a file first.</strong>");
      return;
    }

    const data = new FormData();
    data.append("file", file);
    data.append("document_type", document.getElementById("document_type").value);

    button.disabled = true;
    button.textContent = "Processing…";
    setStatus(
      "warn",
      `<strong>Processing ${esc(file.name)}…</strong>` +
        "<span>Reading the pages and extracting fields. This can take up to " +
        "a minute for a multi-page scan.</span>"
    );

    try {
      const result = await apiFetch(`${API}/documents/process`, {
        method: "POST",
        body: data,
      });
      const failed = result.validation && result.validation.overall_status === "FAIL";
      setStatus(
        failed ? "warn" : "ok",
        `<strong>Processed ${esc(result.document_name)}.</strong>` +
          `<span>${failed
            ? "Extraction succeeded but one or more financial checks failed."
            : "All applicable financial checks passed."}` +
          ` <a href="/documents/${encodeURIComponent(result.document_name)}">View result</a>.</span>`
      );
      form.reset();
      loadDocuments();
    } catch (error) {
      setStatus(
        "error",
        `<strong>${esc(error.code || "ERROR")}</strong><span>${esc(error.message)}</span>`
      );
    } finally {
      button.disabled = false;
      button.textContent = "Process";
    }
  });

  function renderRows(items) {
    if (!items.length) {
      tbody.innerHTML =
        '<tr><td colspan="8" class="empty">No documents processed yet.</td></tr>';
      return;
    }
    tbody.innerHTML = items
      .map((item) => {
        const href = `/documents/${encodeURIComponent(item.document_name)}`;
        return `<tr>
          <td><a href="${href}">${esc(item.document_name)}</a></td>
          <td>${esc(titleise(item.document_type))}</td>
          <td>${statusBadge(item.processing_status)}</td>
          <td>${item.validation_status ? statusBadge(item.validation_status) : "&mdash;"}</td>
          <td>${fmtConfidence(item.overall_confidence)}</td>
          <td>${item.ocr_used
            ? '<span class="badge badge-na">Vision / OCR</span>'
            : '<span class="badge badge-na">Text layer</span>'}</td>
          <td class="num">${item.processing_time_ms != null
            ? (item.processing_time_ms / 1000).toFixed(1) + "s"
            : "&mdash;"}</td>
          <td class="when">${fmtDateTime(item.processed_at)}</td>
        </tr>`;
      })
      .join("");
  }

  async function loadDocuments() {
    const params = new URLSearchParams({ limit: "100" });
    if (search.value.trim()) params.set("search", search.value.trim());
    if (typeFilter.value) params.set("document_type", typeFilter.value);

    try {
      const data = await apiFetch(`${API}/documents?${params}`);
      renderRows(data.items);
      summary.textContent =
        data.total === 0
          ? "Nothing processed yet."
          : `Showing ${data.count} of ${data.total} document${data.total === 1 ? "" : "s"}.`;
    } catch (error) {
      tbody.innerHTML = `<tr><td colspan="8" class="empty">${esc(error.message)}</td></tr>`;
      summary.textContent = "Could not load the document list.";
    }
  }

  let debounce;
  search.addEventListener("input", () => {
    clearTimeout(debounce);
    debounce = setTimeout(loadDocuments, 250);
  });
  typeFilter.addEventListener("change", loadDocuments);
  refresh.addEventListener("click", loadDocuments);

  loadDocuments();
})();
