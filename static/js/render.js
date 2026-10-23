import { state } from "./state.js";

const $ = (id) => document.getElementById(id);

export function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[character]);
}

export function toast(message, isError = false) {
  const element = $("toast");
  element.textContent = message;
  element.classList.toggle("error", isError);
  element.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => element.classList.remove("show"), 3500);
}

function fillSelect(element, values, placeholder) {
  element.innerHTML = [
    `<option value="">${esc(placeholder)}</option>`,
    ...values.map(({ value, label }) => `<option value="${esc(value)}">${esc(label)}</option>`),
  ].join("");
}

function statusClass(status) {
  if (status === "Waiting") return "waiting";
  if (status === "In Progress") return "progress";
  if (status === "Completed" || status === "Paid") return "done";
  return "pending";
}

function renderBookingForm() {
  fillSelect(
    $("bookCentre"),
    state.centres.map((centre) => ({
      value: centre.id,
      label: `${centre.name} (${centre.capacity} bookings/day)`,
    })),
    "Choose a centre"
  );
  fillSelect($("bookSlot"), state.slots.map((slot) => ({ value: slot, label: slot })), "Choose one slot");
  const dateInput = $("bookDate");
  if (!dateInput.value) {
    const localDate = new Date(Date.now() - new Date().getTimezoneOffset() * 60_000)
      .toISOString()
      .slice(0, 10);
    dateInput.value = localDate;
    dateInput.min = localDate;
  }
  $("centresList").innerHTML = state.centres.map((centre) => `
    <article class="centre-card">
      <div><strong>${esc(centre.name)}</strong><p>${centre.capacity} bookings per day</p></div>
      <span class="centre-pin">Centre ${esc(centre.id)}</span>
    </article>`).join("");
}

function renderFarmerBookings() {
  const area = $("farmerBookings");
  if (!state.bookings.length) {
    area.innerHTML = `<div class="panel empty-state"><h2>No bookings yet</h2><p>Choose a procurement centre and reserve one time slot to get a digital token.</p><button class="btn btn-primary" data-view-link="centres">Book a slot</button></div>`;
    return;
  }
  const centreNames = new Map(state.centres.map((centre) => [centre.id, centre.name]));
  area.innerHTML = state.bookings.map((booking) => `
    <article class="panel booking-card">
      <div class="booking-card-heading">
        <div><p class="eyebrow">DIGITAL TOKEN</p><h2>${esc(booking.token)}</h2></div>
        <span class="tag ${statusClass(booking.status)}">${esc(booking.status)}</span>
      </div>
      <dl class="details-grid">
        <div><dt>Centre</dt><dd>${esc(centreNames.get(booking.centre_id) || "Procurement centre")}</dd></div>
        <div><dt>Date &amp; slot</dt><dd>${esc(booking.service_date)} · ${esc(booking.slot)}</dd></div>
        <div><dt>Expected quantity</dt><dd>${esc(booking.quantity_kg)} kg</dd></div>
        <div><dt>Received quantity</dt><dd>${booking.actual_quantity_kg ? `${esc(booking.actual_quantity_kg)} kg` : "Not recorded"}</dd></div>
        <div><dt>Queue position</dt><dd>${booking.queue_position ? `${esc(booking.queue_position)} in this slot` : "—"}</dd></div>
        <div><dt>Estimated wait</dt><dd>${booking.estimated_wait_minutes != null ? `${esc(booking.estimated_wait_minutes)} min` : "—"}</dd></div>
        <div><dt>Payment status</dt><dd><span class="tag ${statusClass(booking.payment)}">${esc(booking.payment)}</span></dd></div>
      </dl>
    </article>`).join("");
}

function renderOfficialDashboard() {
  const analytics = state.analytics;
  if (!analytics) return;
  const summary = analytics.summary;
  const stats = [
    ["Registered farmers", summary.farmers],
    ["Bookings", summary.bookings],
    ["Procurement completed", summary.completed],
    ["Completed, payment pending", summary.pending_payments],
    ["Average booking-to-completion", summary.average_wait_minutes == null ? "—" : `${summary.average_wait_minutes} min`],
  ];
  $("officialStats").innerHTML = stats.map(([label, value]) => `
    <article class="panel stat-card"><span>${esc(label)}</span><strong>${esc(value)}</strong></article>`).join("");
  $("centreLoadBody").innerHTML = analytics.centres.map((centre) => `
    <tr><td>${esc(centre.name)}</td><td>${esc(centre.capacity)}</td><td>${esc(centre.booked_today)}</td><td>${esc(centre.queue_now)}</td></tr>`).join("");
  $("slotLoadBody").innerHTML = analytics.slots.map((slot) => `
    <tr><td>${esc(slot.slot)}</td><td>${esc(slot.bookings)}</td><td>${esc(slot.share_percent)}%</td></tr>`).join("");
  $("noSlotData").classList.toggle("hidden", analytics.slots.length !== 0);
}

function renderOfficialBookings() {
  $("adminBody").innerHTML = state.bookings.map((booking) => {
    const nextStatus = booking.status === "Waiting" ? "In Progress" :
      booking.status === "In Progress" ? "Completed" : null;
    const cancelled = booking.status === "Cancelled";
    return `<tr>
      <td><strong>${esc(booking.token)}</strong><small>${esc(booking.service_date)}</small></td>
      <td>${esc(booking.farmer_name)}<small>${esc(booking.farmer_mobile)}</small></td>
      <td>${esc(booking.centre_name)}<small>${esc(booking.slot)}</small></td>
      <td>${esc(booking.quantity_kg)} kg</td>
      <td><input class="table-input actual-quantity" data-id="${booking.id}" type="number" min="1" max="100000" value="${booking.actual_quantity_kg || ""}" aria-label="Actual quantity received for ${esc(booking.token)}"></td>
      <td><span class="tag ${statusClass(booking.status)}">${esc(booking.status)}</span></td>
      <td><span class="tag ${statusClass(booking.payment)}">${esc(booking.payment)}</span></td>
      <td class="action-cell">
        ${!cancelled ? `<button class="text-button" data-action="save-quantity" data-id="${booking.id}">Save kg</button>` : ""}
        ${nextStatus ? `<button class="text-button" data-action="advance" data-id="${booking.id}">${esc(nextStatus)}</button>` : ""}
        ${booking.status === "Completed" && booking.payment === "Pending" ? `<button class="text-button" data-action="paid" data-id="${booking.id}">Mark paid</button>` : ""}
        ${!cancelled && booking.status !== "Completed" ? `<button class="text-button danger" data-action="cancel" data-id="${booking.id}">Cancel</button>` : ""}
      </td>
    </tr>`;
  }).join("");
  $("adminEmpty").classList.toggle("hidden", state.bookings.length !== 0);
}

function renderOfficialsFarmers() {
  const options = ["Not recorded", "Not applied", "Applied", "Pending", "Approved", "Rejected"];
  $("farmersBody").innerHTML = state.farmers.map((farmer) => {
    const statusOptions = (field, selected) => options.map((option) =>
      `<option value="${esc(option)}" ${selected === option ? "selected" : ""}>${esc(option)}</option>`
    ).join("");
    return `<tr data-farmer-id="${farmer.id}">
      <td><strong>${esc(farmer.name)}</strong></td>
      <td>${esc(farmer.mobile)}<small>${esc(farmer.village)}</small></td>
      <td><input class="table-input kisan-id" maxlength="64" value="${esc(farmer.kisan_farmer_id || "")}" aria-label="Kisan Farmer ID for ${esc(farmer.name)}"></td>
      <td><select class="table-select pm-kisan-status" aria-label="PM-Kisan status for ${esc(farmer.name)}">${statusOptions("pm_kisan_status", farmer.pm_kisan_status)}</select></td>
      <td><select class="table-select pmfby-status" aria-label="PMFBY status for ${esc(farmer.name)}">${statusOptions("pmfby_status", farmer.pmfby_status)}</select></td>
      <td><button class="text-button" data-action="save-farmer" data-id="${farmer.id}">Save</button></td>
    </tr>`;
  }).join("");
}

export function renderAll() {
  if (!state.authenticated) return;
  renderBookingForm();
  if (state.role === "farmer") renderFarmerBookings();
  if (state.role === "official") {
    renderOfficialDashboard();
    renderOfficialBookings();
    renderOfficialsFarmers();
  }
}
