// Sign-in, registration, email confirmation and password reset pages.
// The email address is carried between these pages in sessionStorage (this tab only), never in the URL.

import { ApiError, apiFetch } from "./api.js";
import { clear, el } from "./ui.js";

const EMAIL_KEY = "aquamap.pendingEmail";
const NOTICE_KEY = "aquamap.notice";
const message = document.getElementById("form-message");

function show(kind, text, extra) {
  clear(message);
  if (!text) return;
  message.append(el("p", { class: `msg ${kind}` }, text, extra ? [" ", extra] : null));
}

function describe(error) {
  if (error instanceof ApiError) {
    if (error.errors?.length) return error.errors.map((item) => item.message).join(" ");
    return error.message;
  }
  return "Something went wrong. Please try again.";
}

/** Disable the submit button while a request runs and restore it afterwards. */
async function submitting(form, busyText, work) {
  const button = form.querySelector('button[type="submit"]');
  const label = button.textContent;
  button.disabled = true;
  button.textContent = busyText;
  try {
    await work();
  } catch (error) {
    show("error", describe(error));
  } finally {
    button.disabled = false;
    button.textContent = label;
  }
}

const values = (form) => Object.fromEntries(new FormData(form).entries());
const rememberEmail = (email) => sessionStorage.setItem(EMAIL_KEY, email);
const leaveNotice = (text) => sessionStorage.setItem(NOTICE_KEY, text);

function prefillEmail() {
  const field = document.getElementById("email");
  const saved = sessionStorage.getItem(EMAIL_KEY);
  if (field && saved && !field.value) field.value = saved;
}

function showNotice() {
  const notice = sessionStorage.getItem(NOTICE_KEY);
  if (notice) {
    sessionStorage.removeItem(NOTICE_KEY);
    show("ok", notice);
  }
}

function passwordProblem(password, confirmation) {
  if (password.length < 8) return "The password needs at least 8 characters.";
  if (!/[A-Za-z]/.test(password) || !/[0-9]/.test(password)) return "The password needs at least one letter and one digit.";
  if (password !== confirmation) return "The two passwords do not match.";
  return null;
}

// ---------------------------------------------------------------- sign in
const loginForm = document.getElementById("login-form");
if (loginForm) {
  prefillEmail();
  showNotice();
  loginForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const { email, password } = values(loginForm);
    if (!email || !password) return show("error", "Enter your email and password.");
    submitting(loginForm, "Signing in…", async () => {
      try {
        await apiFetch("/auth/login", { method: "POST", body: { email, password }, redirectOn401: false });
        sessionStorage.removeItem(EMAIL_KEY);
        window.location.assign("/");
      } catch (error) {
        if (error instanceof ApiError && error.code === "email_not_verified") {
          rememberEmail(email);
          show("info", error.message, el("a", { href: "/verify-email" }, "Enter the code"));
          return;
        }
        throw error;
      }
    });
  });
}

// ---------------------------------------------------------------- register
const registerForm = document.getElementById("register-form");
if (registerForm) {
  registerForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const data = values(registerForm);
    if (data.full_name.trim().length < 2) return show("error", "Enter your full name.");
    if (!data.email) return show("error", "Enter your email address.");
    const problem = passwordProblem(data.password, data.confirm_password);
    if (problem) return show("error", problem);
    submitting(registerForm, "Creating account…", async () => {
      const result = await apiFetch("/auth/register", { method: "POST", body: data, redirectOn401: false });
      rememberEmail(result.user.email);
      if (result.verification_required) {
        leaveNotice("Account created. We emailed you a six-digit code.");
        window.location.assign("/verify-email");
      } else {
        leaveNotice("Account created. You can sign in now.");
        window.location.assign("/login");
      }
    });
  });
}

// ---------------------------------------------------------------- confirm email
const verifyForm = document.getElementById("verify-form");
if (verifyForm) {
  prefillEmail();
  showNotice();
  verifyForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const { email, code } = values(verifyForm);
    if (!email) return show("error", "Enter your email address.");
    if (!/^\d{6}$/.test(code)) return show("error", "The code is six digits.");
    submitting(verifyForm, "Checking…", async () => {
      await apiFetch("/auth/verify-email", { method: "POST", body: { email, code }, redirectOn401: false });
      rememberEmail(email);
      leaveNotice("Email confirmed. You can sign in now.");
      window.location.assign("/login");
    });
  });
  const resend = document.getElementById("resend");
  resend.addEventListener("click", async () => {
    const email = document.getElementById("email").value;
    if (!email) return show("error", "Enter your email address first.");
    resend.disabled = true;
    try {
      const result = await apiFetch("/auth/resend-verification", { method: "POST", body: { email }, redirectOn401: false });
      show("ok", `${result.message} You can ask for another code after one minute.`);
    } catch (error) {
      show("error", describe(error));
    } finally {
      setTimeout(() => { resend.disabled = false; }, 5000);
    }
  });
}

// ---------------------------------------------------------------- reset password (two steps on one page)
const forgotForm = document.getElementById("forgot-form");
const resetForm = document.getElementById("reset-form");
if (forgotForm && resetForm) {
  prefillEmail();
  let resetEmail = "";
  forgotForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const { email } = values(forgotForm);
    if (!email) return show("error", "Enter your email address.");
    submitting(forgotForm, "Sending…", async () => {
      const result = await apiFetch("/auth/password/forgot", { method: "POST", body: { email }, redirectOn401: false });
      resetEmail = email;
      forgotForm.hidden = true;
      resetForm.hidden = false;
      show("ok", result.message);
      document.getElementById("code").focus();
    });
  });
  resetForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const data = values(resetForm);
    if (!/^\d{6}$/.test(data.code)) return show("error", "The code is six digits.");
    const problem = passwordProblem(data.new_password, data.confirm_password);
    if (problem) return show("error", problem);
    submitting(resetForm, "Saving…", async () => {
      const result = await apiFetch("/auth/password/reset", { method: "POST", body: { email: resetEmail, ...data }, redirectOn401: false });
      rememberEmail(resetEmail);
      leaveNotice(result.message);
      window.location.assign("/login");
    });
  });
}
