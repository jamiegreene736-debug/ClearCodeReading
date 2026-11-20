document.addEventListener("DOMContentLoaded", () => {
  const role = document.getElementById("id_role");
  const teacher = document.getElementById("id_family_teacher");
  const row = teacher?.closest("[data-family-teacher], .form-row");
  if (!role || !row) return;

  function updateTeacherField() {
    const isParent = role.value === "guardian";
    row.hidden = !isParent;
    teacher.disabled = !isParent;
    teacher.required = isParent;
  }

  role.addEventListener("change", updateTeacherField);
  window.addEventListener("pageshow", updateTeacherField);
  updateTeacherField();
});
