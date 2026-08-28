import { client } from "./generated/client.gen";

client.setConfig({ baseUrl: "" });

const CSRF_COOKIE = "odoo_instance_sdk_csrf";
const CSRF_HEADER = "X-CSRF-Token";

function csrfToken(): string | undefined {
  const cookie = document.cookie
    .split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith(`${CSRF_COOKIE}=`));
  return cookie ? decodeURIComponent(cookie.slice(CSRF_COOKIE.length + 1)) : undefined;
}

client.interceptors.request.use((request) => {
  if (request.method === "POST") {
    const token = csrfToken();
    if (token) request.headers.set(CSRF_HEADER, token);
  }
  return request;
});

export default client;
