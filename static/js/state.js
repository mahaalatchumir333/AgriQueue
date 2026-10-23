export const state = {
  centres: [],
  slots: [],
  crops: [],
  config: { minutesPerToken: 12 },
  user: null,
  role: null,
  authenticated: false,
  csrfToken: "",
  bookings: [],
  farmers: [],
  analytics: null,
};

export async function api(path, options = {}) {
  const method = options.method || "GET";
  const headers = { Accept: "application/json" };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (["POST", "PUT", "PATCH", "DELETE"].includes(method)) {
    headers["X-CSRF-Token"] = state.csrfToken;
  }
  const response = await fetch(path, {
    method,
    headers,
    credentials: "same-origin",
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  const data = await response.json();
  if (!response.ok) {
    const error = new Error(data.error?.message || "The request could not be completed.");
    error.status = response.status;
    throw error;
  }
  if (data.csrf_token) state.csrfToken = data.csrf_token;
  return data;
}

export async function loadState() {
  const [seed, auth] = await Promise.all([
    api("/api/seed"),
    api("/api/auth/session"),
  ]);
  state.centres = seed.centres;
  state.slots = seed.slots;
  state.crops = seed.crops;
  state.config = seed.config;
  state.user = auth.user;
  state.role = auth.role;
  state.authenticated = auth.authenticated;
  if (state.authenticated) await refreshData();
}

export async function refreshData() {
  if (state.role === "farmer") {
    const data = await api("/api/farmer/bookings");
    state.bookings = data.bookings;
    state.centres = data.centres;
  } else if (state.role === "official") {
    const [analytics, bookings, farmers] = await Promise.all([
      api("/api/official/analytics"),
      api("/api/official/bookings"),
      api("/api/admin/farmers"),
    ]);
    state.analytics = analytics;
    state.bookings = bookings.bookings;
    state.farmers = farmers.farmers;
  }
}

export function applyAuth(data) {
  state.authenticated = data.authenticated;
  state.role = data.role;
  state.user = data.user;
  if (data.csrf_token) state.csrfToken = data.csrf_token;
}

export function clearAuth() {
  state.authenticated = false;
  state.role = null;
  state.user = null;
  state.bookings = [];
  state.farmers = [];
  state.analytics = null;
}
