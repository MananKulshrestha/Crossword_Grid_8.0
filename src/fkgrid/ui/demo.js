const form = document.querySelector("#queryForm");
const termInput = document.querySelector("#term");
const localeInput = document.querySelector("#locale");
const categoryInput = document.querySelector("#category");
const runButton = document.querySelector("#runButton");
const runButtonText = document.querySelector("#runButtonText");
const emptyState = document.querySelector("#emptyState");
const loadingState = document.querySelector("#loadingState");
const resultState = document.querySelector("#resultState");
const errorState = document.querySelector("#errorState");
const resultHeading = document.querySelector("#resultHeading");
const runState = document.querySelector("#runState");
const termError = document.querySelector("#termError");
const recentBlock = document.querySelector("#recentBlock");
const recentRuns = document.querySelector("#recentRuns");
const querySet = document.querySelector("#querySet");
const queryCount = document.querySelector("#queryCount");
const fullWorkflow = document.querySelector("#fullWorkflow");
const modeBadge = document.querySelector("#modeBadge");
const modeHint = document.querySelector("#modeHint");
const liveRuns = [];
let latestResponse = null;
let loadingTimer = null;

const $ = (selector) => document.querySelector(selector);

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function setReadyState(state, text) {
  const badge = $("#readyBadge");
  badge.dataset.state = state;
  $("#readyText").textContent = text;
}

async function checkReadiness() {
  try {
    const response = await fetch("/ready", { headers: { Accept: "application/json" } });
    const body = await response.json();
    if (response.ok && body.ready) {
      setReadyState("ready", body.mode === "gemma" ? "Gemma live" : "Demo model");
    } else {
      setReadyState("error", "Model not ready");
    }
  } catch {
    setReadyState("error", "API unavailable");
  }
}

function setPanel(panel) {
  emptyState.hidden = panel !== "empty";
  loadingState.hidden = panel !== "loading";
  resultState.hidden = panel !== "result";
  errorState.hidden = panel !== "error";
}

function setLoadingStep(step) {
  const messages = fullWorkflow.checked
    ? [
        ["Reading your phrase…", "Preparing bounded evidence for the live proposer."],
        ["Gemma is comparing meanings…", "The independent critic is checking scope and precision."],
        ["Verifying the proposal…", "Deterministic gates are checking the candidate before activation."],
      ]
    : [
        ["Reading your phrase…", "Preparing one bounded proposer call for the fast preview."],
        ["Checking the target…", "Deterministic vocabulary and target validation remain enabled."],
        ["Preparing the preview…", "No critic, release gate, review, or activation call will run."],
      ];
  const [title, copy] = messages[step];
  $("#loadingTitle").textContent = title;
  $("#loadingCopy").textContent = copy;
  $("#loadingBar").style.width = `${[18, 54, 82][step]}%`;
  ["loadStepOne", "loadStepTwo", "loadStepThree"].forEach((id, index) => {
    $("#" + id).classList.toggle("active", index === step);
  });
  $("#loadStepTwo").textContent = fullWorkflow.checked ? "CRITIQUE" : "VALIDATE";
  $("#loadStepThree").textContent = fullWorkflow.checked ? "VERIFY" : "PREVIEW";
}

function startLoadingAnimation() {
  let step = 0;
  setLoadingStep(step);
  loadingTimer = window.setInterval(() => {
    step = Math.min(step + 1, 2);
    setLoadingStep(step);
  }, 2200);
}

function stopLoadingAnimation() {
  if (loadingTimer !== null) window.clearInterval(loadingTimer);
  loadingTimer = null;
}

function formatLabel(value) {
  return String(value ?? "").replaceAll("_", " ").toLowerCase().replace(/(^|\s)\S/g, (letter) => letter.toUpperCase());
}

function renderQuerySet(body) {
  const input = body.input ?? {};
  const output = body.output ?? {};
  const expansions = Array.isArray(output.expanded_to) ? output.expanded_to : [];
  const terms = [
    { label: "INITIAL QUERY", value: input.term || "—", meta: "what you entered" },
    { label: "NORMALIZED", value: output.normalized_query || "—", meta: "canonical text form" },
    ...expansions.map((expansion) => ({
      label: "EXPANDED TERM",
      value: expansion.target_id,
      meta: formatLabel(expansion.expansion_action),
    })),
  ];
  queryCount.textContent = `${terms.length} ${terms.length === 1 ? "TERM" : "TERMS"}`;
  querySet.innerHTML = terms.map((term, index) => `<div class="query-row">
    <span class="query-index">${index + 1}</span>
    <span class="query-row-label">${escapeHtml(term.label)}</span>
    <strong class="query-row-value">${escapeHtml(term.value)}</strong>
    <span class="query-row-meta">${escapeHtml(term.meta)}</span>
  </div>`).join("");
}

function renderTimeline(body) {
  const gateLabels = [
    ["PROPOSER", body.model?.proposer_status],
    ["CRITIC", body.model?.critic_status],
    ["VALIDATION", body.gates?.validation],
    ["REGRESSION", body.gates?.regression],
    ["SHADOW", body.gates?.shadow],
    ["ACTIVATION", body.gates?.activation],
  ];
  $("#workflowTimeline").innerHTML = gateLabels.map(([label, status]) => {
    const normalized = String(status ?? "NOT_RUN");
    const complete = ["OK", "VALID", "PASSED", "APPROVED", "ACTIVATED"].includes(normalized);
    const skipped = normalized === "SKIPPED_PREVIEW";
    const failed = ["ERROR", "REJECTED", "ABSTAIN", "NOT_RUN"].includes(normalized);
    return `<div class="timeline-item ${complete ? "complete" : skipped ? "skipped" : failed ? "failed" : "pending"}">
      <div class="timeline-dot" aria-hidden="true"></div>
      <span class="timeline-label">${escapeHtml(label)}</span>
      <span class="timeline-status">${escapeHtml(normalized)}</span>
    </div>`;
  }).join("");
}

function renderResult(body) {
  latestResponse = body;
  const expansion = body.output?.expanded_to?.[0] ?? null;
  const previewOnly = body.status === "PREVIEW_ONLY";
  const input = body.input ?? {};
  $("#shownQuery").textContent = input.term || "—";
  $("#shownLocale").textContent = `${input.locale || "—"} · ${input.category || "global"}`;
  $("#shownTarget").textContent = expansion?.target_id || "No safe target";
  $("#shownAction").textContent = expansion ? formatLabel(expansion.expansion_action) : "ABSTAINED SAFELY";
  $("#normalizedQuery").textContent = body.output?.normalized_query || "—";
  $("#mappingType").textContent = expansion ? formatLabel(expansion.mapping_kind) : "No mapping";
  $("#evidenceBand").textContent = expansion ? formatLabel(expansion.evidence_band) : "Needs review";
  $("#workflowResult").textContent = previewOnly ? "PREVIEW ONLY" : body.gates?.activated ? "COMPLETE" : "SAFE STOP";
  $("#runMeta").textContent = `${body.run_id || "run"} · ${body.model?.mode === "gemma" ? "Gemma active" : "demo model"} · ${body.gates?.active_lexicon_version || "no active version"}`;
  resultHeading.textContent = previewOnly
    ? expansion ? "Fast preview complete" : "No preview target proposed"
    : expansion ? "Here is the safe expansion" : "No safe expansion proposed";
  runState.textContent = previewOnly ? "PREVIEW ONLY" : body.gates?.activated ? "WORKFLOW COMPLETE" : "SAFE STOP";
  runState.dataset.state = previewOnly ? "preview" : body.gates?.activated ? "complete" : "error";
  renderQuerySet(body);
  renderTimeline(body);
  setPanel("result");
  pushRecentRun(input.term, expansion?.target_id, body.gates?.activated);
}

function renderError(title, message) {
  resultHeading.textContent = "The workflow could not complete";
  runState.textContent = "NEEDS ATTENTION";
  runState.dataset.state = "error";
  $("#errorTitle").textContent = title;
  $("#errorCopy").textContent = message;
  setPanel("error");
}

function pushRecentRun(term, target, activated) {
  const existing = liveRuns.findIndex((run) => run.term === term);
  if (existing >= 0) liveRuns.splice(existing, 1);
  liveRuns.unshift({ term, target: target || (activated ? "Activated" : "No target") });
  liveRuns.splice(4);
  recentBlock.hidden = liveRuns.length === 0;
  recentRuns.innerHTML = liveRuns.map((run) => `<button type="button" class="recent-run" data-term="${escapeHtml(run.term)}"><span>${escapeHtml(run.term)}</span><span>${escapeHtml(run.target)}</span></button>`).join("");
  recentRuns.querySelectorAll(".recent-run").forEach((button) => {
    button.addEventListener("click", () => {
      termInput.value = button.dataset.term;
      form.requestSubmit();
    });
  });
}

async function runExpansion(event) {
  event.preventDefault();
  const term = termInput.value.trim();
  const category = categoryInput.value.trim();
  termError.textContent = "";
  if (!term) {
    termError.textContent = "Enter a phrase to expand.";
    termInput.focus();
    return;
  }
  if (!category) {
    termError.textContent = "Add a category scope for this demo.";
    categoryInput.focus();
    return;
  }

  runButton.disabled = true;
  runButtonText.textContent = "Running Gemma…";
  resultHeading.textContent = "Following the decision trail";
  runState.textContent = "RUNNING";
  runState.dataset.state = "running";
  setPanel("loading");
  startLoadingAnimation();
  const params = new URLSearchParams({
    term,
    locale: localeInput.value,
    category,
    proposer_deadline_ms: fullWorkflow.checked ? "30000" : "20000",
  });
  const endpoint = fullWorkflow.checked
    ? "/api/v1/catalog-language/tier2/guided-run"
    : "/api/v1/catalog-language/tier2/guided-preview";
  try {
    const response = await fetch(`${endpoint}?${params.toString()}`, {
      method: "POST",
      headers: { Accept: "application/json" },
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || "The workflow returned an error.");
    renderResult(body);
  } catch (error) {
    renderError("The live run did not finish", error.message || "Check that the API and Gemma provider are ready, then try again.");
  } finally {
    stopLoadingAnimation();
    runButton.disabled = false;
    runButtonText.textContent = "Expand query";
  }
}

form.addEventListener("submit", runExpansion);
$("#retryButton").addEventListener("click", () => form.requestSubmit());
$("#clearRecent").addEventListener("click", () => {
  liveRuns.length = 0;
  recentBlock.hidden = true;
  recentRuns.replaceChildren();
});
$("#copyButton").addEventListener("click", async () => {
  if (!latestResponse) return;
  const expansion = latestResponse.output?.expanded_to?.[0];
  const summary = expansion
    ? `${latestResponse.input.term} → ${expansion.target_id} (${expansion.expansion_action})`
    : `${latestResponse.input.term} → no safe expansion proposed`;
  try {
    await navigator.clipboard.writeText(summary);
    $("#copyButton").innerHTML = "<span aria-hidden=\"true\">✓</span> Copied";
    window.setTimeout(() => { $("#copyButton").innerHTML = "<span aria-hidden=\"true\">▣</span> Copy summary"; }, 1600);
  } catch {
    $("#copyButton").textContent = "Select result above";
  }
});
document.querySelectorAll(".starter-chip").forEach((chip) => {
  chip.addEventListener("click", () => {
    termInput.value = chip.dataset.term;
    termInput.focus();
  });
});

fullWorkflow.addEventListener("change", () => {
  const preview = !fullWorkflow.checked;
  modeBadge.textContent = preview ? "FAST PREVIEW" : "FULL";
  modeBadge.classList.toggle("preview", preview);
  modeHint.textContent = preview
    ? "One live proposer call. Critic and publication gates are skipped; activation is impossible."
    : "Safest path. Runs both model calls and every release gate.";
});

checkReadiness();
