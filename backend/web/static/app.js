(() => {
  const token = new URLSearchParams(window.location.search).get("t");
  if (token) {
    window.history.replaceState({}, document.title, window.location.pathname);
  }

  const $ = (selector) => document.querySelector(selector);
  const elements = {
    runState: $("#run-state"),
    fetch: $("#fetch-button"),
    dryRun: $("#dry-run"),
    queue: $("#queue-list"),
    empty: $("#empty-detail"),
    detail: $("#detail-content"),
    preflight: $("#preflight-button"),
    preflightPanel: $("#preflight-panel"),
    preflightChecks: $("#preflight-checks"),
    summary: $("#run-summary"),
    logs: $("#activity-lines"),
    statusFilter: $("#status-filter"),
    actionFilter: $("#action-filter"),
  };
  const items = new Map();
  const itemDetails = new Map();
  let selectedId = null;
  let redmineUrl = "";

  function node(tag, text, className) {
    const element = document.createElement(tag);
    if (text !== undefined && text !== null) element.textContent = String(text);
    if (className) element.className = className;
    return element;
  }

  async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    headers.set("X-Session-Token", token || "");
    if (options.body) headers.set("Content-Type", "application/json");
    const response = await fetch(path, { ...options, headers });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
    return data;
  }

  function renderQueue() {
    elements.queue.replaceChildren();
    const status = elements.statusFilter.value;
    const action = elements.actionFilter.value;
    const visible = [...items.values()].filter((item) =>
      (!status || item.status === status) &&
      (!action || item.suggested_action === action)
    );
    $("#queue-total").textContent = String(visible.length);
    for (const item of visible) {
      const button = node("button", undefined, "queue-item");
      button.type = "button";
      button.setAttribute("aria-pressed", String(selectedId === item.id));
      if (selectedId === item.id) button.classList.add("selected");
      if (item.status !== "PENDING") button.classList.add("resolved");
      button.addEventListener("click", () => showItem(item.id));
      const top = node("div", undefined, "queue-item-top");
      top.append(node("span", item.sender || "Unknown sender", "sender"));
      top.append(node("span", item.status, `mini-status status-${(item.status || "").toLowerCase()}`));
      button.append(top);
      button.append(node("strong", item.subject || "(No subject)", "queue-subject"));
      const tags = node("div", undefined, "tag-row");
      tags.append(node("span", item.suggested_action || "REVIEW", "action-tag"));
      if (item.is_duplicate) tags.append(node("span", "Duplicate", "tag"));
      if (item.is_reply) tags.append(node("span", "Reply", "tag"));
      if (item.has_attachments) tags.append(node("span", "Attachments", "tag"));
      button.append(tags);
      elements.queue.append(button);
    }
  }

  function appendEvidence(body, evidence) {
    const source = String(body || "");
    const quote = String(evidence || "");
    if (!quote || !source.includes(quote)) {
      return document.createTextNode(source);
    }
    const wrapper = document.createDocumentFragment();
    let offset = 0;
    let index = source.indexOf(quote);
    while (index !== -1) {
      wrapper.append(document.createTextNode(source.slice(offset, index)));
      const mark = node("mark", quote);
      wrapper.append(mark);
      offset = index + quote.length;
      index = source.indexOf(quote, offset);
    }
    wrapper.append(document.createTextNode(source.slice(offset)));
    return wrapper;
  }

  async function showItem(id) {
    if (!items.has(id)) return;
    selectedId = id;
    renderQueue();
    let item = itemDetails.get(id);
    if (!item) {
      try {
        item = await api(`/api/item/${encodeURIComponent(id)}`);
        itemDetails.set(id, item);
      } catch (error) {
        showError(error);
        return;
      }
    }
    if (selectedId !== id) return;
    elements.empty.hidden = true;
    elements.detail.hidden = false;
    elements.detail.replaceChildren();

    const heading = node("div", undefined, "detail-heading");
    const headingText = document.createElement("div");
    headingText.append(node("p", item.sender || "Unknown sender", "eyebrow"));
    headingText.append(node("h2", item.email?.subject || item.subject || "Email review"));
    heading.append(headingText);
    heading.append(node("span", item.status, `mini-status status-${(item.status || "").toLowerCase()}`));
    elements.detail.append(heading);

    const bodySection = node("section", undefined, "detail-section");
    bodySection.append(node("h3", "Original email"));
    const original = node("pre", undefined, "email-body");
    original.append(appendEvidence(item.email?.body, item.classification?.issue_evidence));
    bodySection.append(original);
    elements.detail.append(bodySection);

    const form = document.createElement("form");
    form.className = "review-form";
    const subjectLabel = node("label", "Subject");
    const subjectInput = document.createElement("input");
    subjectInput.name = "subject";
    subjectInput.maxLength = 255;
    subjectInput.value = item.suggested_subject || item.subject || "";
    subjectInput.required = true;
    subjectLabel.append(subjectInput);
    const charCount = node("span", `${subjectInput.value.length}/255 · target 80`, "char-count");
    subjectInput.addEventListener("input", () => {
      charCount.textContent = `${subjectInput.value.length}/255 · target 80`;
      updatePreview();
    });
    subjectLabel.append(charCount);
    form.append(subjectLabel);

    const descriptionLabel = node("label", "Description");
    const descriptionInput = document.createElement("textarea");
    descriptionInput.name = "description";
    descriptionInput.maxLength = 50000;
    descriptionInput.rows = 10;
    descriptionInput.value = item.suggested_description || "";
    descriptionLabel.append(descriptionInput);
    form.append(descriptionLabel);
    descriptionInput.addEventListener("input", updatePreview);

    const classification = item.classification || {};
    const classificationPanel = node("section", undefined, "detail-section classification");
    classificationPanel.append(node("h3", "Classification"));
    const facts = node("dl", undefined, "facts");
    for (const [label, value] of [
      ["Suggested action", item.suggested_action],
      ["Category", classification.category || item.category || "—"],
      ["Confidence", typeof classification.confidence === "number" ? `${Math.round(classification.confidence * 100)}%` : "—"],
      ["Missing fields", (classification.missing_fields || []).join(", ") || "None"],
      ["Attachment-dependent", classification.attachment_dependent ? "Yes" : "No"],
      ["Reasoning", classification.reasoning || "Not available"],
    ]) {
      facts.append(node("dt", label));
      facts.append(node("dd", value));
    }
    classificationPanel.append(facts);
    if (classification.source === "fallback") {
      classificationPanel.append(node("p", "Rule-based fallback was used because AI classification was unavailable.", "notice"));
    }
    form.append(classificationPanel);

    const attachmentSection = node("section", undefined, "detail-section");
    attachmentSection.append(node("h3", "Attachment plan"));
    if ((item.attachment_plans || []).length) {
      const table = document.createElement("table");
      const header = document.createElement("tr");
      for (const label of ["File", "Action", "Size", "Details"]) header.append(node("th", label));
      table.append(header);
      for (const plan of item.attachment_plans) {
        const row = document.createElement("tr");
        row.append(node("td", plan.file));
        row.append(node("td", plan.action));
        row.append(node("td", `${formatBytes(plan.original_size)} → ${formatBytes(plan.estimated_final_size)}`));
        row.append(node("td", [plan.note, plan.local_path].filter(Boolean).join(" · ")));
        table.append(row);
      }
      attachmentSection.append(table);
    } else {
      attachmentSection.append(node("p", "No file attachments detected.", "muted"));
    }
    form.append(attachmentSection);

    const links = item.detected_links || [];
    const linkSection = node("section", undefined, "detail-section");
    linkSection.append(node("h3", "Detected links"));
    if (links.length) {
      const list = document.createElement("ul");
      for (const link of links) {
        const row = document.createElement("li");
        row.append(node("span", `${link.type}: `));
        const anchor = node("a", link.url);
        anchor.href = link.url;
        anchor.rel = "noreferrer noopener";
        anchor.target = "_blank";
        row.append(anchor);
        list.append(row);
      }
      linkSection.append(list);
    } else linkSection.append(node("p", "No recognized external links.", "muted"));
    form.append(linkSection);

    if (item.is_reply || item.is_duplicate) {
      const match = item.existing_issue || {};
      const panel = node("fieldset", undefined, "detail-section match-panel");
      panel.append(node("legend", item.is_reply ? "Possible reply" : "Possible duplicate"));
      if (match.id || item.existing_issue_id) {
        const issueId = match.id || item.existing_issue_id;
        panel.append(node("p", `Matched Redmine ticket #${issueId}: ${match.subject || "Subject unavailable"}`));
        if (match.url) {
          const link = node("a", "Open ticket");
          link.href = match.url;
          link.rel = "noreferrer noopener";
          link.target = "_blank";
          panel.append(link);
        }
      }
      if (item.is_reply) {
        const options = node("div", undefined, "reply-options");
        const createChoice = document.createElement("input");
        createChoice.type = "radio";
        createChoice.name = "reply-choice";
        createChoice.value = "create";
        createChoice.checked = true;
        options.append(createChoice, node("span", "Create a new ticket"));
        const attachChoice = document.createElement("input");
        attachChoice.type = "radio";
        attachChoice.name = "reply-choice";
        attachChoice.value = "attach";
        options.append(attachChoice, node("span", `Attach as note to #${match.id || item.existing_issue_id}`));
        panel.append(options);
        panel.dataset.issueId = String(match.id || item.existing_issue_id || "");
      }
      form.append(panel);
    }

    let previewPre = null;
    if (item.dry_run && (item.dry_run_payload || item.result?.preview)) {
      const preview = node("details", undefined, "detail-section payload-preview");
      preview.append(node("summary", "Dry-run payload preview"));
      previewPre = node("pre");
      preview.append(previewPre);
      form.append(preview);
    }

    const result = item.result || {};
    if (item.error || result.attachment_handling || result.preview) {
      const resultPanel = node("section", undefined, "result-panel");
      resultPanel.append(node("h3", item.error ? "Processing failed" : "Result"));
      if (item.error) resultPanel.append(node("p", item.error, "error-text"));
      if (result.redmine_issue_id && redmineUrl) {
        const issueLink = node("a", `Open Redmine issue #${result.redmine_issue_id}`);
        issueLink.href = `${redmineUrl}/issues/${result.redmine_issue_id}`;
        issueLink.rel = "noreferrer noopener";
        issueLink.target = "_blank";
        resultPanel.append(issueLink);
      }
      for (const line of result.attachment_handling || []) resultPanel.append(node("p", line));
      if (result.preview) {
        resultPanel.append(node("pre", JSON.stringify(result.preview, null, 2), "email-body"));
      }
      if (item.error) {
        const retry = node("button", "Retry", "secondary");
        retry.type = "button";
        retry.addEventListener("click", () => retryItem(item.id));
        resultPanel.append(retry);
      }
      form.append(resultPanel);
    }

    const actions = node("div", undefined, "action-row");
    const approve = node("button", item.dry_run ? "Preview" : "Approve", "primary");
    approve.type = "submit";
    approve.disabled = !subjectInput.value.trim() || item.status !== "PENDING";
    subjectInput.addEventListener("input", () => {
      approve.disabled = !subjectInput.value.trim() || item.status !== "PENDING";
    });
    const reject = node("button", "Reject", "danger");
    reject.type = "button";
    reject.disabled = item.status !== "PENDING";
    reject.addEventListener("click", () => resolveItem(item.id, "reject"));
    const skip = node("button", "Skip", "secondary");
    skip.type = "button";
    skip.disabled = item.status !== "PENDING";
    skip.addEventListener("click", () => resolveItem(item.id, "skip"));
    actions.append(approve, reject, skip);
    form.append(actions);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!subjectInput.value.trim()) return;
      let issueId = null;
      const matchPanel = form.querySelector(".match-panel");
      if (matchPanel && matchPanel.querySelector('input[value="attach"]')?.checked) {
        issueId = Number(matchPanel.dataset.issueId) || null;
      }
      try {
        const response = await api(`/api/item/${encodeURIComponent(item.id)}/approve`, {
          method: "POST",
          body: JSON.stringify({
            subject: subjectInput.value,
            description: descriptionInput.value,
            attach_to_issue_id: issueId,
          }),
        });
        updateLocalStatus(item.id, response.status);
      } catch (error) {
        showError(error);
      }
    });
    elements.detail.append(form);

    function updatePreview() {
      if (!previewPre) return;
      const base = item.result?.preview || item.dry_run_payload;
      if (!base || item.status !== "PENDING") {
        previewPre.textContent = JSON.stringify(base || {}, null, 2);
        return;
      }
      const current = JSON.parse(JSON.stringify(base));
      if (current.issue) {
        current.issue.subject = subjectInput.value;
        const marker = String(current.issue.description || "").match(
          /<!-- gmail-message-id: [A-Za-z0-9_-]+ -->/
        );
        current.issue.description = descriptionInput.value;
        if (marker && !current.issue.description.includes(marker[0])) {
          current.issue.description = `${current.issue.description}\n\n${marker[0]}`;
        }
      }
      previewPre.textContent = JSON.stringify(current, null, 2);
    }
    updatePreview();
  }

  async function resolveItem(id, action) {
    try {
      const response = await api(`/api/item/${encodeURIComponent(id)}/${action}`, {
        method: "POST",
        body: action === "reject" ? "{}" : "{}",
      });
      updateLocalStatus(id, response.status);
    } catch (error) {
      showError(error);
    }
  }

  function updateLocalStatus(id, status) {
    const item = items.get(id);
    if (item) item.status = status;
    const detail = itemDetails.get(id);
    if (detail) detail.status = status;
    renderQueue();
    if (selectedId === id) showItem(id);
  }

  async function retryItem(id) {
    try {
      await api(`/api/item/${encodeURIComponent(id)}/retry`, {
        method: "POST",
        body: "{}",
      });
    } catch (error) {
      showError(error);
    }
  }

  function formatBytes(value) {
    const bytes = Number(value || 0);
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  }

  function showError(error) {
    addLog(error.message, "error");
  }

  function addLog(line, className = "") {
    const row = node("p", line, className);
    elements.logs.prepend(row);
    while (elements.logs.children.length > 100) {
      elements.logs.lastElementChild.remove();
    }
  }

  function renderPreflight(checks) {
    elements.preflightChecks.replaceChildren();
    for (const check of checks || []) {
      const row = node("div", undefined, "check-row");
      row.append(node("span", check.name));
      row.append(node("span", check.status, `check-status status-${check.status.toLowerCase()}`));
      row.append(node("span", check.message));
      elements.preflightChecks.append(row);
    }
    const failed = (checks || []).some((check) => check.status === "FAIL");
    const warned = (checks || []).some((check) => check.status === "WARN");
    elements.preflight.textContent = !checks?.length
      ? "Preflight: skipped"
      : failed ? "Preflight: FAIL" : warned ? "Preflight: WARN" : "Preflight: OK";
    elements.preflight.className = `status-badge ${failed ? "bad" : warned ? "warn" : checks?.length ? "good" : ""}`;
  }

  function renderSummary(summary) {
    elements.summary.hidden = false;
    elements.summary.replaceChildren();
    elements.summary.append(node("strong", "Run summary"));
    const details = [
      `Fetched ${summary.fetched || 0}`,
      `duplicates skipped ${summary.duplicates_skipped || 0}`,
      `ignored ${summary.ignored || 0}`,
      `created ${summary.created || 0}`,
      `attached ${summary.attached || 0}`,
      `failed ${summary.failed || 0}`,
      `AI fallback ${summary.ai_fallback || 0}`,
      `compressed ${summary.attachments_compressed || 0}`,
      `attachments skipped ${summary.attachments_skipped || 0}`,
    ];
    elements.summary.append(node("span", details.join(" · ")));
  }

  let runDefaultsLoaded = false;
  async function loadStatus() {
    const data = await api("/api/status");
    elements.runState.textContent = data.run_state;
    elements.fetch.disabled = data.running;
    if (!runDefaultsLoaded) {
      elements.dryRun.checked = data.dry_run_default;
      runDefaultsLoaded = true;
    }
    redmineUrl = data.redmine_url || "";
    $("#ffmpeg-status").textContent = `FFmpeg: ${data.ffmpeg ? "yes" : "no"}`;
    $("#attachment-limit").textContent = `Attachment limit: ${data.attachment_limit_mb} MB`;
    $("#counts").textContent = `Pending ${data.counts.pending} · Created ${data.counts.created} · Rejected ${data.counts.rejected}`;
    renderPreflight(data.checks);
  }

  async function loadQueue() {
    const queue = await api("/api/queue");
    items.clear();
    for (const summary of queue) items.set(summary.id, summary);
    renderQueue();
  }

  elements.fetch.addEventListener("click", async () => {
    try {
      elements.fetch.disabled = true;
      elements.summary.hidden = true;
      await api("/api/run", {
        method: "POST",
        body: JSON.stringify({ dry_run: elements.dryRun.checked }),
      });
    } catch (error) {
      elements.fetch.disabled = false;
      showError(error);
    }
  });
  elements.preflight.addEventListener("click", () => {
    elements.preflightPanel.hidden = !elements.preflightPanel.hidden;
  });
  elements.statusFilter.addEventListener("change", renderQueue);
  elements.actionFilter.addEventListener("change", renderQueue);

  if (!token) {
    addLog("Session token missing. Reopen the local app from its startup URL.", "error");
    elements.fetch.disabled = true;
    return;
  }

  loadStatus().then(loadQueue).catch(showError);
  const stream = new EventSource(`/api/events?t=${encodeURIComponent(token)}`);
  stream.addEventListener("item_added", async (event) => {
    const summary = JSON.parse(event.data);
    try {
      const item = await api(`/api/item/${encodeURIComponent(summary.id)}`);
      itemDetails.set(item.id, item);
      items.set(item.id, item);
      renderQueue();
      showItem(item.id);
    } catch (error) {
      showError(error);
    }
  });
  for (const eventName of ["item_updated", "item_resolved"]) {
    stream.addEventListener(eventName, async (event) => {
      const update = JSON.parse(event.data);
      if (update.id) {
        try {
          const item = await api(`/api/item/${encodeURIComponent(update.id)}`);
          itemDetails.set(item.id, item);
          items.set(item.id, item);
          renderQueue();
        } catch (error) {
          showError(error);
        }
      }
      loadStatus().catch(showError);
    });
  }
  stream.addEventListener("run_state", (event) => {
    const data = JSON.parse(event.data);
    elements.runState.textContent = data.state;
    elements.fetch.disabled = data.state === "FETCHING" ||
      data.state === "CLASSIFYING" ||
      data.state === "WAITING_FOR_USER";
  });
  stream.addEventListener("run_summary", (event) => {
    renderSummary(JSON.parse(event.data));
    loadStatus().catch(showError);
  });
  stream.addEventListener("log", (event) => {
    const data = JSON.parse(event.data);
    addLog(data.line);
  });
  stream.onerror = () => addLog("Live activity disconnected; reconnecting…", "muted");
})();
