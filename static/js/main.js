import { api, applyAuth, clearAuth, loadState, refreshData, state } from "./state.js";
import { renderAll, toast } from "./render.js";

const $ = (id) => document.getElementById(id);
const VIEWS = {
  farmer: ["farmerDashboard", "centres"],
  official: ["officialDashboard", "officialBookings", "officialFarmers"],
};
let currentView = "farmerDashboard";

function showAuth() {
  $("appShell").classList.add("hidden");
  $("userBar").classList.add("hidden");
  $("authScreen").classList.remove("hidden");
}

function showPortal() {
  $("authScreen").classList.add("hidden");
  $("appShell").classList.remove("hidden");
  $("userBar").classList.remove("hidden");
  document.querySelectorAll(".farmer-only").forEach((element) =>
    element.classList.toggle("hidden", state.role !== "farmer")
  );
  document.querySelectorAll(".official-only").forEach((element) =>
    element.classList.toggle("hidden", state.role !== "official")
  );
  $("userLabel").textContent = state.role === "official"
    ? `Official · ${state.user.username}`
    : `Farmer · ${state.user.name}`;
  currentView = state.role === "official" ? "officialDashboard" : "farmerDashboard";
  showView(currentView);
}

function showView(view) {
  const allowedViews = VIEWS[state.role] || [];
  if (!allowedViews.includes(view)) return;
  currentView = view;
  document.querySelectorAll(".nav-btn").forEach((button) =>
    button.classList.toggle("active", button.dataset.view === view)
  );
  document.querySelectorAll(".view").forEach((section) =>
    section.classList.toggle("hidden", section.id !== `view-${view}`)
  );
  if (view === "centres") loadAvailability();
}

async function reloadPortal() {
  await refreshData();
  renderAll();
}

async function loadAvailability() {
  const centreId = $("bookCentre").value;
  const serviceDate = $("bookDate").value;
  const button = $("bookingForm").querySelector('button[type="submit"]');
  if (!centreId || !serviceDate) return;
  button.disabled = true;
  $("availabilityInfo").textContent = "Checking daily centre capacity…";
  try {
    const params = new URLSearchParams({ centre_id: centreId, service_date: serviceDate });
    const result = await api(`/api/availability?${params.toString()}`);
    const slots = result.slots.map(({ slot, booked, available }) => ({
      value: slot,
      label: `${slot} · ${booked} booked${available ? "" : " · full"}`,
    }));
    const previous = $("bookSlot").value;
    $("bookSlot").innerHTML = [
      '<option value="">Choose one slot</option>',
      ...slots.map(({ value, label }) => `<option value="${escapeAttribute(value)}">${escapeAttribute(label)}</option>`),
    ].join("");
    if (slots.some((slot) => slot.value === previous)) $("bookSlot").value = previous;
    const isAvailable = result.remaining_daily_capacity > 0;
    button.disabled = !isAvailable;
    $("availabilityInfo").textContent = isAvailable
      ? `${result.remaining_daily_capacity} of ${result.daily_capacity} daily booking spaces remain. Slot counts are informational; capacity is shared across the centre's slots.`
      : "This centre is fully booked for the selected date.";
  } catch (error) {
    $("availabilityInfo").textContent = "Availability could not be checked. Refresh and try again.";
    toast(error.message, true);
  }
}

function switchAuthForm(formName) {
  const showRegistration = formName === "register";
  $("loginForm").classList.toggle("hidden", showRegistration);
  $("registerForm").classList.toggle("hidden", !showRegistration);
  $("loginTab").classList.toggle("active", !showRegistration);
  $("registerTab").classList.toggle("active", showRegistration);
  $("loginTab").setAttribute("aria-selected", String(!showRegistration));
  $("registerTab").setAttribute("aria-selected", String(showRegistration));
}

function bindEvents() {
  $("loginTab").addEventListener("click", () => switchAuthForm("login"));
  $("registerTab").addEventListener("click", () => switchAuthForm("register"));

  $("loginForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const result = await api("/api/auth/login", {
        method: "POST",
        body: {
          identifier: $("loginIdentifier").value.trim(),
          password: $("loginPassword").value,
        },
      });
      applyAuth(result);
      await reloadPortal();
      showPortal();
      $("loginForm").reset();
      toast("Signed in successfully.");
    } catch (error) {
      toast(error.message, true);
    }
  });

  $("registerForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const result = await api("/api/farmers", {
        method: "POST",
        body: {
          name: $("fName").value.trim(),
          mobile: $("fMobile").value.trim(),
          village: $("fVillage").value.trim(),
          crop: $("fCrop").value,
          password: $("fPassword").value,
        },
      });
      applyAuth(result);
      await reloadPortal();
      showPortal();
      $("registerForm").reset();
      toast("Farmer account created.");
    } catch (error) {
      toast(error.message, true);
    }
  });

  $("logoutBtn").addEventListener("click", async () => {
    try {
      await api("/api/auth/logout", { method: "POST", body: {} });
      clearAuth();
      await api("/api/auth/session");
      showAuth();
      toast("Signed out.");
    } catch (error) {
      toast(error.message, true);
    }
  });

  document.querySelectorAll(".nav-btn").forEach((button) =>
    button.addEventListener("click", () => showView(button.dataset.view))
  );
  document.addEventListener("click", (event) => {
    const viewButton = event.target.closest("[data-view-link]");
    if (viewButton) showView(viewButton.dataset.viewLink);
  });
  $("bookCentre").addEventListener("change", loadAvailability);
  $("bookDate").addEventListener("change", loadAvailability);

  $("bookingForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const result = await api("/api/bookings", {
        method: "POST",
        body: {
          centre_id: Number($("bookCentre").value),
          service_date: $("bookDate").value,
          slot: $("bookSlot").value,
          quantity_kg: Number($("bookQty").value),
        },
      });
      await reloadPortal();
      showView("farmerDashboard");
      toast(`Booking created. Your token is ${result.booking.token}.`);
    } catch (error) {
      toast(error.message, true);
    }
  });

  $("refreshFarmerBtn").addEventListener("click", async () => {
    try {
      await reloadPortal();
      toast("Queue status refreshed.");
    } catch (error) {
      toast(error.message, true);
    }
  });
  $("refreshOfficialBtn").addEventListener("click", async () => {
    try {
      await reloadPortal();
      toast("Operations data refreshed.");
    } catch (error) {
      toast(error.message, true);
    }
  });

  $("adminBody").addEventListener("click", async (event) => {
    const button = event.target.closest("button[data-action]");
    if (!button) return;
    const bookingId = button.dataset.id;
    const action = button.dataset.action;
    const quantityInput = document.querySelector(`.actual-quantity[data-id="${bookingId}"]`);
    try {
      if (action === "advance") {
        const body = { status: button.textContent.trim() };
        if (body.status === "Completed") {
          body.actual_quantity_kg = Number(quantityInput.value);
        }
        await api(`/api/official/bookings/${bookingId}`, { method: "PATCH", body });
      } else if (action === "save-quantity") {
        await api(`/api/official/bookings/${bookingId}`, {
          method: "PATCH",
          body: { actual_quantity_kg: Number(quantityInput.value) },
        });
      } else if (action === "paid") {
        await api(`/api/official/bookings/${bookingId}`, {
          method: "PATCH",
          body: { payment: "Paid" },
        });
      } else if (action === "cancel") {
        await api(`/api/official/bookings/${bookingId}`, { method: "DELETE", body: {} });
      }
      await reloadPortal();
      toast("Booking updated.");
    } catch (error) {
      toast(error.message, true);
    }
  });

  $("farmersBody").addEventListener("click", async (event) => {
    const button = event.target.closest('button[data-action="save-farmer"]');
    if (!button) return;
    const row = button.closest("tr");
    try {
      await api(`/api/admin/farmers/${button.dataset.id}`, {
        method: "PATCH",
        body: {
          kisan_farmer_id: row.querySelector(".kisan-id").value.trim() || null,
          pm_kisan_status: row.querySelector(".pm-kisan-status").value,
          pmfby_status: row.querySelector(".pmfby-status").value,
        },
      });
      await reloadPortal();
      toast("Farmer details updated.");
    } catch (error) {
      toast(error.message, true);
    }
  });

  window.setInterval(async () => {
    if (!state.authenticated || document.hidden) return;
    try {
      await reloadPortal();
    } catch (error) {
      if (error.status === 401) {
        clearAuth();
        showAuth();
        toast("Your session expired. Please sign in again.", true);
      } else {
        toast(error.message, true);
      }
    }
  }, 30_000);
}

async function init() {
  try {
    await loadState();
    fillCrops();
    bindEvents();
    if (state.authenticated) {
      renderAll();
      showPortal();
    } else {
      showAuth();
    }
  } catch (error) {
    console.error(error);
    toast("Could not load the portal. Refresh the page to try again.", true);
  }
}

function fillCrops() {
  $("fCrop").innerHTML = [
    '<option value="">Choose a crop</option>',
    ...state.crops.map((crop) => `<option value="${escapeAttribute(crop)}">${escapeAttribute(crop)}</option>`),
  ].join("");
}

function escapeAttribute(value) {
  return String(value).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[character]);
}

init();
