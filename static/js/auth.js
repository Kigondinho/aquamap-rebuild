// Login, registration and verification forms.
// A later phase wires each form to its endpoint; until then submissions are intercepted so that the
// browser does not post to routes that return 501.

const form = document.querySelector("form");
const errorBox = document.getElementById("form-error");

if (form) {
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (errorBox) {
      errorBox.textContent = "Sign-in is not available yet (authentication is built in a later phase).";
    }
  });
}
