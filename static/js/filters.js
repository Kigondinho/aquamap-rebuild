// Reads the sidebar filter controls.

/** Currently selected risk tiers, in DOM order. */
export function readSelectedTiers() {
  return Array.from(document.querySelectorAll('input[name="tier"]:checked')).map((el) => el.value);
}

/** Call ``onChange`` whenever a tier checkbox changes. */
export function bindFilterChanges(onChange) {
  document.querySelectorAll('input[name="tier"]').forEach((el) => el.addEventListener("change", onChange));
}
