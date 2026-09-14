(() => {
  const form = document.querySelector("#resource-form");
  if (!form) return;
  const state = document.querySelector("#save-state");
  const errors = document.querySelector("#save-errors");
  let generation = 0;
  let savedGeneration = 0;
  let saving = null;
  let timer;
  let leaving = false;
  let conflict = false;
  function flattenErrors(value) {
    if (Array.isArray(value)) return value.map(flattenErrors).join(" ");
    if (value && typeof value === "object")
      return Object.values(value).map(flattenErrors).join(" ");
    return String(value);
  }
  async function save() {
    if (conflict) return false;
    if (saving) {
      if (!(await saving)) return false;
      return save();
    }
    if (generation === savedGeneration) return true;
    const currentGeneration = generation;
    const data = new FormData(form);
    const uploads = [...form.querySelectorAll("input[type=file]")].map(
      (input) => [input, input.files[0]],
    );
    data.set("autosave", "1");
    state.textContent = "Saving draft…";
    errors.hidden = true;
    saving = (async () => {
      try {
        const response = await fetch(form.action, {
          method: "POST",
          body: data,
          credentials: "same-origin",
          signal: AbortSignal.timeout(60000),
        });
        if (
          response.redirected ||
          !response.headers.get("content-type")?.includes("application/json")
        )
          throw new Error(
            "Your session may have expired. Save a copy of your text, then sign in again.",
          );
        const result = await response.json();
        if (!response.ok) {
          conflict = response.status === 409;
          throw new Error(
            flattenErrors(result.errors || "The draft could not be saved."),
          );
        }
        document.querySelectorAll("input[name=version]").forEach((input) => {
          input.value = result.version;
        });
        for (const [name, value] of [
          ["asset", result.asset_id],
          ["cover", result.cover_id],
        ]) {
          const input = form.elements.namedItem(name);
          if (input && input.value === (data.get(name) || "")) {
            if (
              input.tagName === "SELECT" &&
              value &&
              ![...input.options].some((option) => option.value === value)
            )
              input.add(new Option(result.asset_name, value));
            input.value = value || "";
          }
        }
        if (form.elements.title.value === data.get("title"))
          form.elements.title.value = result.title;
        for (const [input, uploaded] of uploads)
          if (input.files[0] === uploaded) input.value = "";
        const removeCover = form.elements.namedItem("remove_cover");
        if (removeCover && data.get("remove_cover") && removeCover.checked)
          removeCover.checked = false;
        savedGeneration = currentGeneration;
        state.textContent = `Draft saved · ${new Date(result.saved_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
        return true;
      } catch (error) {
        state.textContent = "Not saved — your edits are still on this page.";
        errors.textContent =
          error.name === "TimeoutError"
            ? "Saving timed out. Check your connection and select Save draft to retry."
            : error.message;
        errors.hidden = false;
        return false;
      }
    })();
    const success = await saving;
    saving = null;
    if (success && generation !== savedGeneration) return save();
    return success;
  }
  function edited() {
    generation++;
    state.textContent = "Unsaved changes";
    clearTimeout(timer);
    timer = setTimeout(save, 1100);
  }
  form.addEventListener("input", edited);
  form.addEventListener("change", edited);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    clearTimeout(timer);
    // Explicit save also retries an interrupted request with unchanged fields.
    if (generation === savedGeneration) generation++;
    await save();
  });
  document.querySelectorAll(".resource-action").forEach((actionForm) => {
    actionForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (leaving) return;
      leaving = true;
      const submitter = event.submitter;
      clearTimeout(timer);
      if (await save()) {
        if (submitter?.name) {
          const input = document.createElement("input");
          input.type = "hidden";
          input.name = submitter.name;
          input.value = submitter.value;
          actionForm.append(input);
        }
        HTMLFormElement.prototype.submit.call(actionForm);
      } else {
        leaving = false;
        errors.scrollIntoView({ block: "center", behavior: "smooth" });
      }
    });
  });
  window.addEventListener("beforeunload", (event) => {
    if (!leaving && generation !== savedGeneration) {
      event.preventDefault();
      event.returnValue = "";
    }
  });
  const preview = document.querySelector("#preview-button");
  const dialog = document.querySelector("#preview-dialog");
  const frame = document.querySelector("#preview-frame");
  preview.addEventListener("click", async (event) => {
    if (typeof dialog.showModal !== "function") return;
    event.preventDefault();
    if (!(await save())) {
      errors.scrollIntoView({ block: "center" });
      return;
    }
    frame.src = `${preview.href}?t=${Date.now()}`;
    dialog.showModal();
  });
  document
    .querySelector("#preview-close")
    .addEventListener("click", () => dialog.close());
  for (const size of ["desktop", "mobile"]) {
    document.querySelector(`#preview-${size}`).addEventListener("click", () => {
      frame.classList.toggle("mobile", size === "mobile");
      document
        .querySelector("#preview-mobile")
        .setAttribute("aria-pressed", String(size === "mobile"));
      document
        .querySelector("#preview-desktop")
        .setAttribute("aria-pressed", String(size === "desktop"));
    });
  }
  const copy = document.querySelector("#copy-link");
  copy?.addEventListener("click", async () => {
    const url = new URL(copy.dataset.path, window.location.origin).href;
    try {
      await navigator.clipboard.writeText(url);
      copy.textContent = "Link copied";
    } catch {
      copy.textContent = url;
    }
  });
})();
