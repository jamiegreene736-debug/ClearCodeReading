(() => {
  const form = document.querySelector("#bulk-form");
  if (!form) return;
  const files = document.querySelector("#files");
  const zone = document.querySelector("#drop-zone");
  const summary = document.querySelector("#file-summary");
  function checkFiles() {
    const total = [...files.files].reduce((sum, file) => sum + file.size, 0);
    const invalid =
      files.files.length > 10 ||
      total > 50 * 1024 * 1024 ||
      [...files.files].some((file) => file.size > 10 * 1024 * 1024);
    files.setCustomValidity(
      invalid ? "Choose up to 10 files, 10 MB each and 50 MB total." : "",
    );
    summary.textContent = invalid
      ? files.validationMessage
      : `${files.files.length} file(s) selected · ${(total / 1024 / 1024).toFixed(1)} MB`;
  }
  files.addEventListener("change", checkFiles);
  for (const event of ["dragover", "dragenter"])
    zone.addEventListener(event, (e) => {
      e.preventDefault();
      zone.classList.add("dragging");
    });
  for (const event of ["dragleave", "drop"])
    zone.addEventListener(event, () => zone.classList.remove("dragging"));
  zone.addEventListener("drop", (event) => {
    event.preventDefault();
    files.files = event.dataTransfer.files;
    checkFiles();
  });
  form.addEventListener("submit", () => {
    document.querySelector("#upload-button").disabled = true;
    document.querySelector("#upload-state").textContent =
      "Uploading files… Keep this page open until your drafts are ready.";
  });
})();
